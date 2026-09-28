{
    'name': 'ElitePoints Loyalty',
    'version': '18.0.1.0.0',
    'category': 'Point of Sale',
    'summary': 'Reward and redeem ElitePoints loyalty points from Odoo Point of Sale',
    'description': """
ElitePoints Loyalty
====================

Connects your Odoo Point of Sale to the ElitePoints loyalty network.

* Configure your store's ElitePoints API key and secret in Settings.
* Look up a customer by phone, email, or loyalty barcode at the POS register.
* Show the customer's live ElitePoints balance during checkout.
* Redeem points against the current sale.
* Points are earned automatically on the remaining amount when an order is paid.
* Failed syncs are queued and retried automatically, so a network hiccup never
  blocks checkout.

Requires an active ElitePoints merchant account. Visit https://myelitepoints.com
to sign up.
""",
    'author': 'S7enTech',
    'website': 'https://myelitepoints.com',
    'license': 'LGPL-3',
    'depends': ['point_of_sale'],
    'data': [
        'security/ir.model.access.csv',
        'views/res_config_settings_views.xml',
        'views/pos_order_views.xml',
        'data/product_data.xml',
        'data/ir_cron_data.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'elitepoints_loyalty/static/src/js/**/*',
            'elitepoints_loyalty/static/src/xml/**/*',
            'elitepoints_loyalty/static/src/scss/**/*',
        ],
    },
    'images': ['static/description/banner.png'],
    'installable': True,
    'application': False,
}
