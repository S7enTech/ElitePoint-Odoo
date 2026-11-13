#!/bin/sh
# Generates /etc/odoo/odoo.conf at boot from the environment, then hands off to
# the official Odoo entrypoint.
#
# Why this exists: the base image ships no `admin_passwd`, so Odoo falls back to
# its built-in default. That setting is the only thing gating
# /web/database/manager — backup, restore, duplicate and delete — so a default
# password on a reachable URL is a full database compromise.
#
# Setting it through the web UI is not enough. Odoo persists the value by
# rewriting odoo.conf, and with no volume behind /etc/odoo that file reverts on
# the next restart, silently restoring the default. Generating the config at boot
# makes the setting durable, and keeps the password out of the image and out of
# git. The guard below turns a missing password into a failed deploy rather than
# a quiet fallback to the default.
set -e

: "${ODOO_ADMIN_PASSWD:?refusing to start: set ODOO_ADMIN_PASSWD (Odoo master password) on the service}"

# list_db defaults closed. With it off Odoo cannot render a database selector, so
# either pin one database with ODOO_DBFILTER (e.g. '^mydb$') or set
# ODOO_LIST_DB=True for as long as you actually need the selector.
cat > /etc/odoo/odoo.conf <<EOF
[options]
addons_path = /mnt/extra-addons
data_dir = /var/lib/odoo
admin_passwd = ${ODOO_ADMIN_PASSWD}
list_db = ${ODOO_LIST_DB:-False}
EOF

if [ -n "${ODOO_DBFILTER:-}" ]; then
    echo "dbfilter = ${ODOO_DBFILTER}" >> /etc/odoo/odoo.conf
fi

# The base image already owns this file as `odoo` and we run as `odoo`, so the
# redirect above preserves ownership and no chown is possible or needed. This
# chmod is cosmetic tightening only, so never let it abort the boot.
chmod 640 /etc/odoo/odoo.conf 2>/dev/null || true

exec /entrypoint.sh "$@"
