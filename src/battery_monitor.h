// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include <stdint.h>

class BatteryMonitor
{
public:
    struct SampleResult
    {
        bool valid = false;
        uint32_t millivolts = 0;
        uint8_t halfPercent = 0;
        bool low = false;
    };

    // Battery presence is an explicit build choice. USB can energize the
    // charger's BAT node even with no cell fitted, so voltage is not proof.
    bool Init();
    SampleResult Sample();

private:
    bool mReady = false;
};
