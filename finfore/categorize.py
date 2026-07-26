"""Transaction categorization.

Two mechanisms:
1. Keyword rules applied to the raw description.
2. Transfer pairing: opposite-signed equal amounts across the user's own
   accounts within a small date window are internal transfers, which must be
   excluded from income/spending statistics (they don't change net worth).
"""
from __future__ import annotations

import pandas as pd

# (keywords, category) — first match wins. Keywords are matched against the
# upper-cased raw description.
RULES: list[tuple[tuple[str, ...], str]] = [
    (("PAYROLL", "DIRECT DEP", "SALARY", "PAYCHECK", "EMPLOYER"), "Income"),
    (("INTEREST", "DIVIDEND"), "Interest & dividends"),
    (("TRANSFER", "XFER"), "Transfer"),
    (("RENT", "LANDLORD", "PROPERTIES", "APARTMENT", "MORTGAGE"), "Housing"),
    (("ELECTRIC", "POWER CO", "ENERGY", "GAS & ELEC", "WASHINGTON GAS",
      "NATURAL GAS", "WATER", "SEWER", "UTILIT", "WASTE MGMT",
      "WASTE MANAGEMENT", "REPUBLIC SERV", "CON EDISON", "CONED",
      "NATIONAL GRID", "PG&E", "PGANDE", "DOMINION"), "Utilities"),
    (("INTERNET", "COMCAST", "XFINITY", "SPECTRUM", "FIOS", "BROADBAND"), "Utilities"),
    (("VERIZON", "T-MOBILE", "TMOBILE", "AT&T", "ATT WIRELESS", "MINT MOBILE"), "Utilities"),
    (("NETFLIX", "SPOTIFY", "HULU", "DISNEY", "HBO", "MAX.COM", "YOUTUBE PREM",
      "APPLE.COM/BILL", "AUDIBLE", "SUBSTACK", "PATREON", "CRUNCHYROLL",
      "PARAMOUNT", "PEACOCK", "PRIME VIDEO", "APPLETV", "SLING", "TWITCH"), "Subscriptions"),
    (("GEICO", "STATE FARM", "PROGRESSIVE", "ALLSTATE", "INSURANCE", "USAA INS"), "Insurance"),
    # Card payments belong here too. They are the most common outflow the
    # rules used to miss entirely, and when only the current account has been
    # imported there is no opposite side for transfer-pairing to find, so they
    # sat in Misc forever. When both sides *are* imported, pair_transfers still
    # runs afterwards and relabels them Transfer, which is the better answer.
    # Anchored forms only: bare "DISCOVER" catches Discovery+, bare "VISA"
    # catches visa fees.
    (("LOAN", "NAVIENT", "NELNET", "SALLIE", "MOHELA", "AIDVANTAGE",
      "EDFINANCIAL", "AUTO FIN", "AUTO FINANCE", "CAR PAYMENT", "LENDING",
      "CARD PMT", "CARD PAYMENT", "CRD PYMT", "CRD EPAY", "CARD MIN PMT",
      "CREDIT CRD", "AMEX EPAYMENT", "DISCOVER E-PAY", "DISCOVER PAYMENT",
      "CHASE CARD", "CAPITAL ONE CRD", "CARDMEMBER SERV"), "Debt payment"),
    # "MARKET" on its own was matching FACEBOOK MARKETPLACE and anything with
    # "stock market" in it, filing both as groceries. A miss is recoverable in
    # one click and is remembered; a confident mislabel quietly skews the score.
    (("KROGER", "SAFEWAY", "WEGMANS", "PUBLIX", "ALDI", "H-E-B", "HEB ", "WINCO",
      "TRADER JOE", "WHOLE FOODS", "FOOD LION", "GIANT FOOD", "GROCER",
      "SUPERMARKET", "FOOD MARKET", "FRESH MARKET", "FARMERS MARKET",
      "MARKET BASKET", "SPROUTS", "HARRIS TEETER", "MEIJER", "HYVEE",
      "HY-VEE", "STOP & SHOP", "COSTCO", "SAM'S CLUB"), "Groceries"),
    (("STARBUCKS", "DUNKIN", "COFFEE", "CAFE", "RESTAURANT", "PIZZA", "SUSHI",
      "TACO", "BURGER", "CHIPOTLE", "MCDONALD", "WENDY", "CHICK-FIL",
      "DOORDASH", "UBER EATS", "GRUBHUB", "BREWERY", "BAR & GRILL", "DELI",
      "PANERA", "FIVE GUYS", "SHAKE SHACK", "PANDA EXPRESS", "SUBWAY",
      "BAKERY", "THAI", "RAMEN", "BBQ"), "Dining"),
    (("SHELL", "EXXON", "CHEVRON", "MARATHON", "BP#", "BP ", "SUNOCO", "WAWA",
      "SHEETZ", "FUEL", "GAS STATION", "SPEEDWAY", "CIRCLE K", "QT ", "PILOT"), "Fuel"),
    (("UBER", "LYFT", "TRANSIT", "PARKING", "TOLL", "AMTRAK"), "Transport"),
    (("AMAZON", "AMZN", "TARGET", "WALMART", "BEST BUY", "EBAY", "ETSY",
      "HOME DEPOT", "LOWES", "IKEA", "MICRO CENTER", "MARKETPLACE",
      "REI ", "REI.COM", "NORDSTROM", "MACY'S", "MACYS", "KOHL'S", "KOHLS",
      "TJ MAXX", "TJMAXX", "MARSHALLS", "ROSS STORES", "OLD NAVY", "WAYFAIR",
      "CHEWY", "PETSMART", "PETCO", "ACE HARDWARE", "MENARDS", "STAPLES",
      "OFFICE DEPOT", "DICK'S SPORTING", "ULTA", "SEPHORA"), "Shopping"),
    (("GYM", "FITNESS", "CLIMB", "YOGA", "CROSSFIT"), "Health & fitness"),
    (("PHARMACY", "CVS", "WALGREEN", "RITE AID", "DENTAL", "MEDICAL", "CLINIC",
      "URGENT CARE", "OPTOMETR", "LABCORP"), "Health & fitness"),
    (("AIRLINE", "AIRLINES", "DELTA AIR", "UNITED AIR", "SOUTHWEST AIR", "AMERICAN AIR", "ALASKA AIR",
      "HOTEL", "AIRBNB", "MARRIOTT", "HILTON", "EXPEDIA"), "Travel"),
    (("STEAM", "PLAYSTATION", "NINTENDO", "XBOX", "CINEMA", "THEATRE", "THEATER",
      "TICKETMASTER", "AMC "), "Entertainment"),
    # Giving. Placed last so a specific merchant rule wins first — a bookshop
    # named "...FOUNDATION" should not be swept in ahead of Shopping.
    (("CHARIT", "DONAT", "DONOR", "RED CROSS", "UNICEF", "UNITED WAY",
      "SALVATION ARMY", "GOODWILL", "HABITAT FOR HUM", "ST JUDE",
      "ST. JUDE", "DOCTORS WITHOUT", "WORLD WILDLIFE", "SIERRA CLUB",
      "PLANNED PARENT", "FOOD BANK", "HUMANE SOCIETY", "ASPCA",
      "GOFUNDME", "TITHE", "TITHING", "PARISH", "SYNAGOGUE", "MOSQUE",
      "MISSION FUND", "NONPROFIT", "NON-PROFIT", "501C3", "BENEVOLENCE",
      "ALUMNI FUND", "PLEDGE DRIVE", "NPR PLEDGE", "PBS PLEDGE",
      # "FOUNDATION" and "OFFERING" alone are too broad — they catch
      # bookshops and cosmetics. Anchored variants only.
      "CHARITABLE FOUND", "COMMUNITY FOUND", "MEMORIAL FOUND",
      "RELIEF FUND", "CHURCH OF", "FIRST CHURCH", "CHURCH DONAT"),
     "Charitable donation"),
]

INCOME_CATEGORIES = {"Income", "Interest & dividends", "Other income"}


def categorize(description: str, amount: float) -> str:
    d = (description or "").upper()
    for keywords, cat in RULES:
        if any(k in d for k in keywords):
            return cat
    return "Other income" if amount > 0 else "Misc"


def apply_categories(df: pd.DataFrame) -> pd.DataFrame:
    """Add/overwrite a 'category' column on a transactions frame."""
    df = df.copy()
    df["category"] = [categorize(d, a) for d, a in zip(df["description"], df["amount"])]
    return df


def pair_transfers(df: pd.DataFrame, window_days: int = 4) -> pd.Series:
    """Return a boolean Series marking rows that pair up as internal transfers.

    A pair = two rows in *different* accounts, amounts a and -a (to the cent),
    dated within `window_days`. Greedy one-to-one matching, earliest first.
    """
    mark = pd.Series(False, index=df.index)
    if df.empty or df["account_id"].nunique() < 2:
        return mark
    work = df[["account_id", "date", "amount"]].copy()
    work["cents"] = (work["amount"] * 100).round().astype("int64")
    work = work.sort_values("date")
    from collections import defaultdict, deque
    # queue of candidate rows by |cents|, holding (index, date, cents, account)
    open_by_amt: dict[int, deque] = defaultdict(deque)
    for idx, row in work.iterrows():
        a = int(row["cents"])
        q = open_by_amt[abs(a)]
        # drop stale candidates outside the window
        while q and (row["date"] - q[0][1]).days > window_days:
            q.popleft()
        matched = None
        for i, (j, jd, jc, jacct) in enumerate(q):
            if jc == -a and jacct != row["account_id"]:
                matched = i
                break
        if matched is not None:
            j = q[matched][0]
            del q[matched]
            mark.loc[idx] = True
            mark.loc[j] = True
        else:
            q.append((idx, row["date"], a, row["account_id"]))
    return mark
