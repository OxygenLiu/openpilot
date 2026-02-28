  # SpeedLimitState — claims CustomReserved0
  speedLimit @0 :Float32;          # km/h recommended speed limit
  source @1 :Source;               # which tier provided this
  confirmed @2 :Bool;             # user tapped to confirm
  confidence @3 :Float32;         # 0.0-1.0

  osmMaxspeed @4 :UInt16;         # 0 = unavailable
  yoloSpeed @5 :UInt16;           # 0 = unavailable
  inferredSpeed @6 :UInt16;       # always available

  highwayType @7 :Text;           # motorway/trunk/primary/...
  roadName @8 :Text;
  laneCount @9 :UInt8;

  enum Source {
    osmMaxspeed @0;
    yoloDetection @1;
    roadTypeInference @2;
  }
