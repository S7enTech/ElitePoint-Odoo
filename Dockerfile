# Test harness for elitepoints_loyalty against a real Odoo 18: the official Odoo
# image with our module baked in as a local addon.
#
# Not a production deployment, but no longer dependent on Odoo's default master
# password — entrypoint-wrapper.sh generates /etc/odoo/odoo.conf at boot and
# refuses to start without ODOO_ADMIN_PASSWD. Keep this service on Railway's
# private network; it has no business holding a public domain.
#
# Note: the base image sets `USER odoo`, so build steps and the entrypoint both
# run unprivileged. Nothing here may chown, or chmod a root-owned file — the
# executable bit on entrypoint-wrapper.sh comes from the file in git instead.
FROM odoo:18.0-20260803

COPY elitepoints_loyalty /mnt/extra-addons/elitepoints_loyalty
COPY entrypoint-wrapper.sh /usr/local/bin/entrypoint-wrapper.sh

ENTRYPOINT ["/usr/local/bin/entrypoint-wrapper.sh"]
CMD ["odoo"]
