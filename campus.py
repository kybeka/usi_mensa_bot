DEFAULT_CAMPUS = "usi_lugano_est"

# Map campus key -> (display name, menu URL)
CAMPUS_INFO = {
    DEFAULT_CAMPUS: ("USI Lugano Est", "https://sv-gastronomie.ch/menu/polo%20universitario,%20campus%20est,%20Lugano/Mittagsmen%C3%BC"),
    "usi_lugano_west": ("USI Lugano Ovest", "https://sv-gastronomie.ch/menu/polo%20universitario,%20campus%20ovest,%20Lugano/Mittagsmen%C3%BC"),
    "usi_mendrisio": ("USI Mendrisio", "https://sv-gastronomie.ch/menu/usi%20mendrisio/Mittagsmen%C3%BC"),
    # Add more campuses as needed
}


def get_menu_url(campus_key: str) -> str:
    return CAMPUS_INFO.get(campus_key, CAMPUS_INFO[DEFAULT_CAMPUS])[1]


def get_display_name(campus_key: str) -> str:
    return CAMPUS_INFO.get(campus_key, CAMPUS_INFO[DEFAULT_CAMPUS])[0]
