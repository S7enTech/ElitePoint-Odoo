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
            raise UserError(
                _("ElitePoints did not respond in time. Please try again.")
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            raise UserError(
                _(
                    "Could not reach ElitePoints. Check your internet "
                    "connection and try again."
                )
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise UserError(_("ElitePoints request failed: %s") % exc) from exc

        if response.status_code >= 400:
            message = self._extract_error_message(response)
            _logger.warning(
                "ElitePoints API error %s on %s %s: %s",
                response.status_code,
                method,
                path,
                message,
            )
            raise UserError(_("ElitePoints error: %s") % message)

        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise UserError(
                _("ElitePoints returned an unreadable response.")
            ) from exc

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
            raise UserError(
                _("ElitePoints did not respond in time. Please try again.")
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            raise UserError(
                _(
                    "Could not reach ElitePoints. Check your internet "
                    "connection and try again."
                )
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise UserError(_("ElitePoints request failed: %s") % exc) from exc

        if response.status_code == 401 and not _retried:
            # Cached token was rejected (expired early, or revoked
            # server-side) — force a fresh one and retry exactly once.
            self._authenticate(pos_config, api_key, api_secret, base_url, force=True)
            return self._authed_request(
                pos_config, method, path, json_body=json_body, _retried=True
            )

        if response.status_code >= 400:
            message = self._extract_error_message(response)
            _logger.warning(
                "ElitePoints API error %s on %s %s: %s",
                response.status_code,
                method,
                path,
                message,
            )
            raise UserError(_("ElitePoints error: %s") % message)

        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise UserError(
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
    ):
        body = {
            "customerId": customer_ref,
            "amount": amount,
            "description": description,
            "transactionDate": transaction_date,
            "items": items or [],
        }
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
    ):
        body = {
            "customerId": customer_ref,
            "amount": amount,
            "redeemAmount": redeem_amount,
            "description": description,
            "items": items or [],
        }
        if payment_method:
            body["paymentMethod"] = payment_method
        if external_transaction_id:
            body["externalTransactionId"] = external_transaction_id
        result = self._authed_request(
            pos_config, "POST", "/odoo/points/redeem", json_body=body
        )
        return result.get("data") or {}
