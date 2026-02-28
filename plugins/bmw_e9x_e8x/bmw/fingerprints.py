from bmw.values import CAR
from opendbc.car.structs import CarParams

Ecu = CarParams.Ecu


FINGERPRINTS = {
  CAR.BMW_E82: [{}],  # Empty - VIN detection only
  CAR.BMW_E90: [{}],  # Empty - VIN detection only
}

# Dummy FW entries prevent exact matching collision, forcing VIN fuzzy matching
FW_VERSIONS = {
  CAR.BMW_E82: {(Ecu.fwdRadar, 0x7e0, None): [b'\x00BMW_E82_DUMMY']},
  CAR.BMW_E90: {(Ecu.fwdRadar, 0x7e0, None): [b'\x00BMW_E90_DUMMY']},
}
