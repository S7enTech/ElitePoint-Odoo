#!/bin/sh
# Translates the same HOST/PORT/USER/PASSWORD env vars the prebuilt odoo
# image reads into odoo-bin's --db_* flags, so this source build is a
# drop-in replacement on the same Railway service variables.
exec python3 /opt/odoo/odoo-bin \
  --addons-path=/opt/odoo/addons,/opt/odoo/odoo/addons,/opt/odoo/custom-addons \
  --data-dir=/var/lib/odoo \
  --db_host="$HOST" \
  --db_port="$PORT" \
  --db_user="$USER" \
  --db_password="$PASSWORD" \
  --http-port=8069 \
  --proxy-mode
