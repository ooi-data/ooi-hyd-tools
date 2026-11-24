"""
The OOI hydrophones this package processes, by refdes: site name, approximate position, and
for low-frequency hydrophones the Earthscope station ooipy requests. The README's table
mirrors HYDBB. Import-free, so any module can use it.
"""
from typing import NamedTuple


class Hydrophone(NamedTuple):
    site: str
    lat: float
    lon: float
    station: str | None = None  # Earthscope station code, low-frequency hydrophones only


# broadband, from the OOI raw data server
HYDBB = {
    "CE02SHBP-LJ01D-11-HYDBBA106": Hydrophone("Oregon Shelf", 44.63721, -124.30564),
    "CE04OSBP-LJ01C-11-HYDBBA105": Hydrophone("Oregon Offshore", 44.36933, -124.95347),
    "RS01SLBS-LJ01A-09-HYDBBA102": Hydrophone("Slope Base Seafloor", 44.51505, -125.39002),
    "RS01SBPS-PC01A-08-HYDBBA103": Hydrophone("Slope Base Platform", 44.51516, -125.3899),
    "RS03AXBS-LJ03A-09-HYDBBA302": Hydrophone("Axial Base Seafloor", 45.81676, -129.75426),
    "RS03AXPS-PC03A-08-HYDBBA303": Hydrophone("Axial Base Platform", 45.81671, -129.75405),
}

# low frequency, from Earthscope via ooipy
HYDLF = {
    "RS01SLBS-MJ01A-05-HYDLFA101": Hydrophone("Slope Base", 44.50829, -125.40466, "HYSB1"),
    "RS03AXBS-MJ03A-05-HYDLFA301": Hydrophone("Axial Base", 45.82051, -129.73671, "AXBA1"),
    "RS01SUM1-LJ01B-05-HYDLFA104": Hydrophone("Southern Hydrate Ridge Summit", 44.56922, -125.14816, "HYS14"),
    "RS03CCAL-MJ03F-06-HYDLFA305": Hydrophone("Axial Central Caldera", 45.95479, -130.00932, "AXCC1"),
    "RS03ECAL-MJ03E-09-HYDLFA304": Hydrophone("Axial Eastern Caldera", 45.93997, -129.73383, "AXEC2"),
}
