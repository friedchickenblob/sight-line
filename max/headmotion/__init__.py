"""SightLine head motion + contact maths (Max).

    frame        shared floor-frame convention, wrap()
    sources      Muse S over BrainFlow / CSV replay / synthetic, one interface
    yaw          gyro -> head yaw: tilt fix, bias, integration, resets, camera blend
    contact      time to contact, closest approach, bearing, hysteresis flag
    health       Bluetooth drop counting + watchdog
    synth        fake Muse + fake camera for testing without hardware
    contact_sim  the five synthetic machine scenes
    analysis     experiment analysis (drift, axis, scale, fast turns, glances, blend)
    calibration  measured constants and which are still placeholders
    bus          MQTT: publish our messages, read Hanh's camera yaw
    cli          command-line options shared by the scripts
"""
