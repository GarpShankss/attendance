"""
Edit this file to add/remove/rename locations or warehouses.
Both the upload-assignment screen and the filter dropdowns read from here
at request time via GET /warehouses - nothing is hardcoded in the frontend.
"""
LOCATIONS = {
    "Hyderabad": [
        "Havells", "Sony", "APL", "Unicharm", "TPT",
        "Idemitsu", "Daikin", "Mitsubishi", "Schindler",
    ],
    "Bangalore": [
        "APL", "Havells", "Sony", "Unicharm", "Bajaj", "ABB",
        "Bosch", "LG", "TPT", "Shaw Floor", "Haier",
    ],
}
