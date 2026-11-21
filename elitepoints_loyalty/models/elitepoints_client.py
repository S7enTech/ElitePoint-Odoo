import logging
import time

import requests

from odoo import models
from odoo.exceptions import UserError
from odoo.tools.translate import _

_logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.myelitepoints.com/v1/api"
REQUEST_TIMEOUT = 15
# Refresh the cached token slightly before it actually expires so an
# in-flight order never gets caught mid-refresh.
TOKEN_EXPIRY_BUFFER_SECONDS = 60

# HTTP statuses where the server refused the request itself rather than
# failed to serve it. An identical retry can only ever earn the identical
# refusal, so pos.order's sync marks these terminal instead of handing
# them to the retry cron — see _sync_elitepoints there.
#
# Deliberately narrow, because the two ways of getting this wrong do not
# cost the same. A transient failure wrongly called permanent is a sync
# abandoned for good and points a customer never receives; a permanent
# refusal wrongly called transient is eight wasted cron attempts and some
# log noise — which is the thing being fixed here, and much the cheaper
# mistake. So this holds only statuses that are a pure function of the
# request body, which the sync rebuilds identically on every attempt:
#
#   400 - the backend's own validation refusals: a transaction currency
#         outside SUPPORTED_TRANSACTION_CURRENCIES, an amount that does
#         not match the sum of its line items, a refund larger than what
#         is still reversible on the sale it refunds.
#   422 - not returned by any ElitePoints endpoint today. Included
#         because it means the same thing, so an endpoint that later
#         prefers it is classified right without a change here.
#
# Everything else keeps the retry behaviour it has always had, including
# the 4xx that look permanent and are not:
#
#   401/403 - a rejected or revoked store credential. _authed_request
#             already force-refreshes the token and retries a 401 once,
#             so getting here means the key itself is wrong. An admin
#             re-entering it makes the next cron attempt succeed, which
#             is exactly what a terminal state would prevent.
#   404     - usually permanent (a customer ref that does not exist), but
#             not reliably: a reversal 404s when the sale it reverses has
#             not landed in the backend yet, and a later attempt fixes
#             that on its own.
#   408/429 - transient by definition.
#   5xx     - transient by definition.
PERMANENT_REFUSAL_STATUS_CODES = frozenset({400, 422})


def _api_error(message, status_code=None):
    """Builds the UserError this client raises for a failed request,
    tagged with the HTTP status it came from.

    The status rides on a plain UserError rather than on a UserError
    subclass deliberately. Odoo serialises an exception to the web client
    under its fully-qualified name, and the client chooses which dialog to
    show by looking that name up in its `error_dialogs` registry — where
    `odoo.exceptions.UserError` is registered and an addon's own subclass
    is not. A subclass would therefore risk downgrading the clean warning
    an admin gets from Configure ElitePoints Credentials > Test Connection
    into a raw traceback dialog. Tagging an attribute instead leaves the
    exception's identity exactly what it was before this change, so there
    is no frontend behaviour to re-verify.

    Read it back with elitepoints_status_code(), which reports None for
    an exception that never carried one — every non-HTTP failure (timeout,
    DNS, connection reset, unreadable body, a shop with no credentials
    entered yet) arrives that way, and None is treated as transient.
    """
    error = UserError(message)
    error.elitepoints_status_code = status_code
    return error


def elitepoints_status_code(exc):
    """The HTTP status an exception from this client carries, or None."""
    return getattr(exc, "elitepoints_status_code", None)


def is_permanent_refusal(exc):
    """True when retrying the identical request can only be refused again.

    Anything this cannot positively identify as a refusal — including
    every exception that did not come from this client at all — is
    reported as not permanent, so an unrecognised failure keeps the retry
    behaviour it has today rather than being quietly abandoned.
    """
    return elitepoints_status_code(exc) in PERMANENT_REFUSAL_STATUS_CODES


class ElitePointsClient(models.AbstractModel):
    """Thin HTTP client for the ElitePoints ERP integration API.

    Every call takes the pos.config (shop) it's acting on, because
    credentials — and so the cached access token — are per-shop: a
    merchant with several physical stores sharing one Odoo database needs
    each shop authenticating as its own distinct ElitePoints store, not
    all of them sharing one identity. The actual credential storage lives
    on elitepoints.pos.credential, not on pos.config itself — see that
    model's docstring for why. See the README for the full history (this
    was previously a single company-wide ir.config_parameter, then briefly
    plain fields on pos.config, which leaked secrets to the POS frontend).
    """

    _name = "elitepoints.client"
    _description = "ElitePoints API Client"

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def _get_credentials(self, pos_config):
        credential = pos_config.sudo()._elitepoints_get_credential()
        api_key = credential.api_key
        api_secret = credential.api_secret
        base_url = (credential.base_url if credential else None) or DEFAULT_BASE_URL
        if not api_key or not api_secret:
            raise UserError(
                _(
                    "ElitePoints is not configured for this shop yet. Go to "
                    "Point of Sale > Configuration > Point of Sale, open "
                    "%s, and enter your ElitePoints API key and secret "
                    "under ElitePoints Loyalty."
                )
                % pos_config.name
            )
        return api_key, api_secret, base_url.rstrip("/")

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def _authenticate(self, pos_config, api_key, api_secret, base_url, force=False):
        credential = pos_config.sudo()._elitepoints_get_credential(
            create_if_missing=True
        )

        if not force:
            cached_token = credential.access_token
            cached_expiry = credential.token_expires_at
            if (
                cached_token
                and cached_expiry
                and cached_expiry - TOKEN_EXPIRY_BUFFER_SECONDS > time.time()
            ):
                return cached_token

        response = self._raw_request(
            "POST",
            base_url,
            "/odoo/auth",
            json_body={"apiKey": api_key, "apiSecret": api_secret},
            headers={},
        )
        data = response.get("data") or {}
        access_token = data.get("accessToken")
        expires_in = data.get("expiresIn", 0)
        if not access_token:
            raise UserError(
                _("ElitePoints authentication failed: no access token returned.")
            )

        credential.write(
            {
                "access_token": access_token,
                "token_expires_at": time.time() + expires_in,
            }
        )
        return access_token

    def is_configured(self, pos_config):
        """Cheap, no-network check the POS frontend can call on session load
        to decide whether to show the ElitePoints button at all."""
        credential = pos_config.sudo()._elitepoints_get_credential()
        return bool(credential.api_key and credential.api_secret)

    def test_connection(self, pos_config):
        """Verifies the credentials currently configured on this shop.

        Raises UserError with a human-readable message on failure, returns
        True on success. Never trusts a cached token so a stale-but-cached
        session can't report a false positive.
        """
        api_key, api_secret, base_url = self._get_credentials(pos_config)
        self._authenticate(pos_config, api_key, api_secret, base_url, force=True)
        return True

    # ------------------------------------------------------------------
    # Low-level request plumbing
    # ------------------------------------------------------------------

    def _raw_request(self, method, base_url, path, json_body=None, headers=None):
        url = f"{base_url}{path}"
        try:
            response = requests.request(
                method,
                url,
                json=json_body,
                headers=headers or {},
                timeout=REQUEST_TIMEOUT,
            )
        except requests.exceptions.Timeout as exc:
            raise _api_error(
                _("ElitePoints did not respond in time. Please try again.")
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            raise _api_error(
                _(
                    "Could not reach ElitePoints. Check your internet "
                    "connection and try again."
                )
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise _api_error(_("ElitePoints request failed: %s") % exc) from exc

        if response.status_code >= 400:
            self._raise_for_status(response, method, path)

        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise _api_error(
                _("ElitePoints returned an unreadable response.")
            ) from exc

    def _raise_for_status(self, response, method, path):
        """Turns a >= 400 response into the exception both request methods
        raise. Always raises; never returns. Shared so the status code they
        now attach cannot drift between them — before this it was the same
        ten lines twice.
        """
        message = self._extract_error_message(response)
        _logger.warning(
            "ElitePoints API error %s on %s %s: %s",
            response.status_code,
            method,
            path,
            message,
        )
        raise _api_error(
            _("ElitePoints error: %s") % message,
            status_code=response.status_code,
        )

    @staticmethod
    def _extract_error_message(response):
        try:
            body = response.json()
        except ValueError:
            return response.text or f"HTTP {response.status_code}"
        if isinstance(body, dict):
            return body.get("message") or body.get("error") or str(body)
        return str(body)

    def _authed_request(self, pos_config, method, path, json_body=None, _retried=False):
        api_key, api_secret, base_url = self._get_credentials(pos_config)
        token = self._authenticate(pos_config, api_key, api_secret, base_url)
        headers = {"Authorization": f"Bearer {token}"}

        url = f"{base_url}{path}"
        try:
            response = requests.request(
                method, url, json=json_body, headers=headers, timeout=REQUEST_TIMEOUT
            )
        except requests.exceptions.Timeout as exc:
            raise _api_error(
                _("ElitePoints did not respond in time. Please try again.")
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            raise _api_error(
                _(
                    "Could not reach ElitePoints. Check your internet "
                    "connection and try again."
                )
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise _api_error(_("ElitePoints request failed: %s") % exc) from exc

        if response.status_code == 401 and not _retried:
            # Cached token was rejected (expired early, or revoked
            # server-side) — force a fresh one and retry exactly once.
            self._authenticate(pos_config, api_key, api_secret, base_url, force=True)
            return self._authed_request(
                pos_config, method, path, json_body=json_body, _retried=True
            )

        if response.status_code >= 400:
            self._raise_for_status(response, method, path)

        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise _api_error(
                _("ElitePoints returned an unreadable response.")
            ) from exc

    # ------------------------------------------------------------------
    # Public API surface
    # ------------------------------------------------------------------

    def lookup_customer(
        self, pos_config, identifier, id_type, first_name=None, last_name=None
    ):
        """id_type is one of 'email', 'phone', 'barcode'."""
        body = {"identifier": identifier, "type": id_type}
        if first_name:
            body["firstName"] = first_name
        if last_name:
            body["lastName"] = last_name
        result = self._authed_request(
            pos_config, "POST", "/odoo/customer/lookup", json_body=body
        )
        return result.get("data") or {}

    def get_customer_balance(self, pos_config, customer_ref):
        result = self._authed_request(
            pos_config, "GET", f"/odoo/customer/{customer_ref}/balance"
        )
        return result.get("data") or {}

    def earn_points(
        self,
        pos_config,
        customer_ref,
        amount,
        description,
        transaction_date,
        payment_method=None,
        items=None,
        external_transaction_id=None,
        currency=None,
    ):
        body = {
            "customerId": customer_ref,
            "amount": amount,
            "description": description,
            "transactionDate": transaction_date,
            "items": items or [],
        }
        if currency:
            body["currency"] = currency
        if payment_method:
            body["paymentMethod"] = payment_method
        if external_transaction_id:
            body["externalTransactionId"] = external_transaction_id
        result = self._authed_request(
            pos_config, "POST", "/odoo/points/earn", json_body=body
        )
        return result.get("data") or {}

    def redeem_points(
        self,
        pos_config,
        customer_ref,
        amount,
        redeem_amount,
        description,
        payment_method=None,
        items=None,
        external_transaction_id=None,
        currency=None,
    ):
        body = {
            "customerId": customer_ref,
            "amount": amount,
            "redeemAmount": redeem_amount,
            "description": description,
            "items": items or [],
        }
        if currency:
            body["currency"] = currency
        if payment_method:
            body["paymentMethod"] = payment_method
        if external_transaction_id:
            body["externalTransactionId"] = external_transaction_id
        result = self._authed_request(
            pos_config, "POST", "/odoo/points/redeem", json_body=body
        )
        return result.get("data") or {}

    def reverse_transaction(
        self,
        pos_config,
        original_external_transaction_id,
        refund_amount,
        description,
        external_transaction_id=None,
        currency=None,
    ):
        body = {
            "originalExternalTransactionId": original_external_transaction_id,
            "refundAmount": refund_amount,
            "description": description,
        }
        if currency:
            body["currency"] = currency
        if external_transaction_id:
            body["externalTransactionId"] = external_transaction_id
        result = self._authed_request(
            pos_config, "POST", "/odoo/points/reverse", json_body=body
        )
        return result.get("data") or {}
