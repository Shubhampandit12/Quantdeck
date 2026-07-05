"""S&P 500 constituent loader.

``load_sp500()`` returns a list of :class:`SymbolInfo` for the S&P 500. It
optionally attempts a live fetch of the current constituents (Wikipedia via
pandas, if that stack happens to be installed and reachable) but it ALWAYS
falls back to a large embedded static list so the platform works fully offline.

The embedded list below is a curated snapshot of well-known S&P 500 tickers
(> 100 names). It is intentionally static and self-contained: no network, no
disk, no surprises. Exchanges are best-effort ("NASDAQ" vs "NYSE"); when a
dynamic fetch is unavailable we simply label the primary listing venue we know.
"""
from __future__ import annotations

import logging

from ..core.enums import AssetClass
from ..core.models import SymbolInfo

log = logging.getLogger("quantdeck.marketdata.sp500")

# --------------------------------------------------------------------------- #
# Embedded static fallback: (symbol, company name, exchange).                 #
# > 100 real, well-known S&P 500 constituents. This is the source of truth    #
# when a live fetch is not possible (i.e. almost always, and always offline). #
# --------------------------------------------------------------------------- #
_SP500_STATIC: tuple[tuple[str, str, str], ...] = (
    ("AAPL", "Apple Inc.", "NASDAQ"),
    ("MSFT", "Microsoft Corporation", "NASDAQ"),
    ("NVDA", "NVIDIA Corporation", "NASDAQ"),
    ("AMZN", "Amazon.com, Inc.", "NASDAQ"),
    ("GOOGL", "Alphabet Inc. Class A", "NASDAQ"),
    ("GOOG", "Alphabet Inc. Class C", "NASDAQ"),
    ("META", "Meta Platforms, Inc.", "NASDAQ"),
    ("BRK.B", "Berkshire Hathaway Inc. Class B", "NYSE"),
    ("TSLA", "Tesla, Inc.", "NASDAQ"),
    ("LLY", "Eli Lilly and Company", "NYSE"),
    ("AVGO", "Broadcom Inc.", "NASDAQ"),
    ("JPM", "JPMorgan Chase & Co.", "NYSE"),
    ("UNH", "UnitedHealth Group Incorporated", "NYSE"),
    ("V", "Visa Inc.", "NYSE"),
    ("XOM", "Exxon Mobil Corporation", "NYSE"),
    ("MA", "Mastercard Incorporated", "NYSE"),
    ("JNJ", "Johnson & Johnson", "NYSE"),
    ("PG", "Procter & Gamble Company", "NYSE"),
    ("HD", "Home Depot, Inc.", "NYSE"),
    ("COST", "Costco Wholesale Corporation", "NASDAQ"),
    ("ABBV", "AbbVie Inc.", "NYSE"),
    ("MRK", "Merck & Co., Inc.", "NYSE"),
    ("CVX", "Chevron Corporation", "NYSE"),
    ("WMT", "Walmart Inc.", "NYSE"),
    ("KO", "Coca-Cola Company", "NYSE"),
    ("PEP", "PepsiCo, Inc.", "NASDAQ"),
    ("BAC", "Bank of America Corporation", "NYSE"),
    ("ADBE", "Adobe Inc.", "NASDAQ"),
    ("CRM", "Salesforce, Inc.", "NYSE"),
    ("NFLX", "Netflix, Inc.", "NASDAQ"),
    ("AMD", "Advanced Micro Devices, Inc.", "NASDAQ"),
    ("TMO", "Thermo Fisher Scientific Inc.", "NYSE"),
    ("CSCO", "Cisco Systems, Inc.", "NASDAQ"),
    ("ACN", "Accenture plc", "NYSE"),
    ("LIN", "Linde plc", "NASDAQ"),
    ("MCD", "McDonald's Corporation", "NYSE"),
    ("ABT", "Abbott Laboratories", "NYSE"),
    ("DHR", "Danaher Corporation", "NYSE"),
    ("WFC", "Wells Fargo & Company", "NYSE"),
    ("TXN", "Texas Instruments Incorporated", "NASDAQ"),
    ("INTC", "Intel Corporation", "NASDAQ"),
    ("QCOM", "QUALCOMM Incorporated", "NASDAQ"),
    ("PM", "Philip Morris International Inc.", "NYSE"),
    ("VZ", "Verizon Communications Inc.", "NYSE"),
    ("INTU", "Intuit Inc.", "NASDAQ"),
    ("CAT", "Caterpillar Inc.", "NYSE"),
    ("IBM", "International Business Machines Corporation", "NYSE"),
    ("AMGN", "Amgen Inc.", "NASDAQ"),
    ("NOW", "ServiceNow, Inc.", "NYSE"),
    ("GE", "General Electric Company", "NYSE"),
    ("UNP", "Union Pacific Corporation", "NYSE"),
    ("SPGI", "S&P Global Inc.", "NYSE"),
    ("HON", "Honeywell International Inc.", "NASDAQ"),
    ("ISRG", "Intuitive Surgical, Inc.", "NASDAQ"),
    ("PFE", "Pfizer Inc.", "NYSE"),
    ("GS", "Goldman Sachs Group, Inc.", "NYSE"),
    ("BKNG", "Booking Holdings Inc.", "NASDAQ"),
    ("AMAT", "Applied Materials, Inc.", "NASDAQ"),
    ("LOW", "Lowe's Companies, Inc.", "NYSE"),
    ("T", "AT&T Inc.", "NYSE"),
    ("RTX", "RTX Corporation", "NYSE"),
    ("BLK", "BlackRock, Inc.", "NYSE"),
    ("SYK", "Stryker Corporation", "NYSE"),
    ("ELV", "Elevance Health, Inc.", "NYSE"),
    ("DE", "Deere & Company", "NYSE"),
    ("SCHW", "Charles Schwab Corporation", "NYSE"),
    ("PLD", "Prologis, Inc.", "NYSE"),
    ("TJX", "TJX Companies, Inc.", "NYSE"),
    ("C", "Citigroup Inc.", "NYSE"),
    ("BSX", "Boston Scientific Corporation", "NYSE"),
    ("MDT", "Medtronic plc", "NYSE"),
    ("ADP", "Automatic Data Processing, Inc.", "NASDAQ"),
    ("VRTX", "Vertex Pharmaceuticals Incorporated", "NASDAQ"),
    ("CB", "Chubb Limited", "NYSE"),
    ("MMC", "Marsh & McLennan Companies, Inc.", "NYSE"),
    ("LMT", "Lockheed Martin Corporation", "NYSE"),
    ("REGN", "Regeneron Pharmaceuticals, Inc.", "NASDAQ"),
    ("ADI", "Analog Devices, Inc.", "NASDAQ"),
    ("MU", "Micron Technology, Inc.", "NASDAQ"),
    ("AMT", "American Tower Corporation", "NYSE"),
    ("PANW", "Palo Alto Networks, Inc.", "NASDAQ"),
    ("KLAC", "KLA Corporation", "NASDAQ"),
    ("SBUX", "Starbucks Corporation", "NASDAQ"),
    ("BX", "Blackstone Inc.", "NYSE"),
    ("GILD", "Gilead Sciences, Inc.", "NASDAQ"),
    ("BA", "Boeing Company", "NYSE"),
    ("SO", "Southern Company", "NYSE"),
    ("MDLZ", "Mondelez International, Inc.", "NASDAQ"),
    ("PGR", "Progressive Corporation", "NYSE"),
    ("DIS", "Walt Disney Company", "NYSE"),
    ("CI", "Cigna Group", "NYSE"),
    ("DUK", "Duke Energy Corporation", "NYSE"),
    ("TGT", "Target Corporation", "NYSE"),
    ("CME", "CME Group Inc.", "NASDAQ"),
    ("SNPS", "Synopsys, Inc.", "NASDAQ"),
    ("CDNS", "Cadence Design Systems, Inc.", "NASDAQ"),
    ("EOG", "EOG Resources, Inc.", "NYSE"),
    ("SLB", "Schlumberger Limited", "NYSE"),
    ("ITW", "Illinois Tool Works Inc.", "NYSE"),
    ("MO", "Altria Group, Inc.", "NYSE"),
    ("ZTS", "Zoetis Inc.", "NYSE"),
    ("AON", "Aon plc", "NYSE"),
    ("APH", "Amphenol Corporation", "NYSE"),
    ("NOC", "Northrop Grumman Corporation", "NYSE"),
    ("FCX", "Freeport-McMoRan Inc.", "NYSE"),
    ("CL", "Colgate-Palmolive Company", "NYSE"),
    ("PYPL", "PayPal Holdings, Inc.", "NASDAQ"),
    ("USB", "U.S. Bancorp", "NYSE"),
    ("WM", "Waste Management, Inc.", "NYSE"),
    ("MCK", "McKesson Corporation", "NYSE"),
    ("EQIX", "Equinix, Inc.", "NASDAQ"),
    ("GD", "General Dynamics Corporation", "NYSE"),
    ("TDG", "TransDigm Group Incorporated", "NYSE"),
    ("EMR", "Emerson Electric Co.", "NYSE"),
    ("CSX", "CSX Corporation", "NASDAQ"),
    ("MAR", "Marriott International, Inc.", "NASDAQ"),
    ("APD", "Air Products and Chemicals, Inc.", "NYSE"),
    ("NSC", "Norfolk Southern Corporation", "NYSE"),
    ("ORCL", "Oracle Corporation", "NYSE"),
    ("PNC", "PNC Financial Services Group, Inc.", "NYSE"),
    ("FDX", "FedEx Corporation", "NYSE"),
    ("HCA", "HCA Healthcare, Inc.", "NYSE"),
    ("ROP", "Roper Technologies, Inc.", "NASDAQ"),
    ("MMM", "3M Company", "NYSE"),
    ("F", "Ford Motor Company", "NYSE"),
    ("GM", "General Motors Company", "NYSE"),
    ("COF", "Capital One Financial Corporation", "NYSE"),
    ("AJG", "Arthur J. Gallagher & Co.", "NYSE"),
    ("CARR", "Carrier Global Corporation", "NYSE"),
    ("PH", "Parker-Hannifin Corporation", "NYSE"),
    ("OXY", "Occidental Petroleum Corporation", "NYSE"),
    ("PCAR", "PACCAR Inc", "NASDAQ"),
    ("MET", "MetLife, Inc.", "NYSE"),
    ("PSX", "Phillips 66", "NYSE"),
    ("AZO", "AutoZone, Inc.", "NYSE"),
    ("TT", "Trane Technologies plc", "NYSE"),
    ("ADSK", "Autodesk, Inc.", "NASDAQ"),
    ("WELL", "Welltower Inc.", "NYSE"),
    ("SPG", "Simon Property Group, Inc.", "NYSE"),
    ("NXPI", "NXP Semiconductors N.V.", "NASDAQ"),
    ("MSI", "Motorola Solutions, Inc.", "NYSE"),
    ("CTAS", "Cintas Corporation", "NASDAQ"),
    ("ECL", "Ecolab Inc.", "NYSE"),
    ("O", "Realty Income Corporation", "NYSE"),
    ("KMB", "Kimberly-Clark Corporation", "NYSE"),
    ("CMG", "Chipotle Mexican Grill, Inc.", "NYSE"),
    ("DELL", "Dell Technologies Inc.", "NYSE"),
    ("CCI", "Crown Castle Inc.", "NYSE"),
    ("MPC", "Marathon Petroleum Corporation", "NYSE"),
    ("HLT", "Hilton Worldwide Holdings Inc.", "NYSE"),
    ("AEP", "American Electric Power Company, Inc.", "NASDAQ"),
    ("ROST", "Ross Stores, Inc.", "NASDAQ"),
    ("PAYX", "Paychex, Inc.", "NASDAQ"),
    ("KVUE", "Kenvue Inc.", "NYSE"),
    ("TEL", "TE Connectivity Ltd.", "NYSE"),
    ("WMB", "Williams Companies, Inc.", "NYSE"),
)


def _static_symbols() -> list[SymbolInfo]:
    """Build SymbolInfo objects from the embedded static list."""
    return [
        SymbolInfo(
            symbol=sym,
            name=name,
            asset_class=AssetClass.US_EQUITY,
            exchange=exch,
            provider_symbol=sym,
            tradable=True,
        )
        for sym, name, exch in _SP500_STATIC
    ]


def _try_dynamic() -> list[SymbolInfo]:
    """Best-effort live fetch of current S&P 500 constituents.

    Uses pandas' HTML table reader against Wikipedia if both pandas and a
    network connection are available. Any failure (import error, no network,
    layout change) simply returns an empty list so the caller falls back to
    the embedded static list. This never raises.
    """
    try:
        import pandas as pd  # type: ignore

        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        tables = pd.read_html(url)  # network + HTML parse
        df = tables[0]
        # Column names on that page: "Symbol", "Security", ...
        sym_col = "Symbol" if "Symbol" in df.columns else df.columns[0]
        name_col = "Security" if "Security" in df.columns else df.columns[1]
        out: list[SymbolInfo] = []
        for _, row in df.iterrows():
            raw = str(row[sym_col]).strip().upper()
            if not raw or raw == "NAN":
                continue
            # Wikipedia uses "BRK.B"; keep the canonical dotted form.
            out.append(
                SymbolInfo(
                    symbol=raw,
                    name=str(row[name_col]).strip(),
                    asset_class=AssetClass.US_EQUITY,
                    exchange="NASDAQ/NYSE",
                    provider_symbol=raw,
                    tradable=True,
                )
            )
        # Sanity check: a real fetch has ~500 rows. Anything much smaller is
        # suspect, so we discard it and let the caller use the static list.
        if len(out) >= 100:
            return out
        return []
    except Exception as exc:  # noqa: BLE001 - dynamic fetch is strictly optional
        log.debug("S&P 500 dynamic fetch unavailable, using static list: %s", exc)
        return []


def load_sp500() -> list[SymbolInfo]:
    """Return the S&P 500 constituents as SymbolInfo.

    Attempts a dynamic fetch first, but ALWAYS falls back to the embedded
    static list (>= 100 tickers) so the platform functions offline.
    """
    dynamic = _try_dynamic()
    if dynamic:
        return dynamic
    return _static_symbols()
