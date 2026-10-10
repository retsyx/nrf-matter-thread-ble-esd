// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include <stdint.h>

#include "bma400.h"

// Bosch SensorAPI with the Zephyr I2C device selected by mailbox_bma400.
// Access from the application thread, never a GPIO ISR. The application owns
// the retry/hold timers and masks INT1 during the five-second motion pulse.
class Bma400Motion
{
public:
    int8_t Init();
    int8_t ReadInterruptStatus(uint16_t & status);
    int8_t ReadSample(bma400_sensor_data & sample);
    bool IsReady() const { return mReady; }
    int8_t LastError() const { return mLastError; }
    uint8_t ChipId() const { return mDevice.chip_id; }

    Bma400Motion() = default;
    Bma400Motion(const Bma400Motion &) = delete;
    Bma400Motion & operator=(const Bma400Motion &) = delete;

private:
    int8_t ConfigureMotion();
    static BMA400_INTF_RET_TYPE ReadRegisters(uint8_t reg, uint8_t * data,
                                            uint32_t length, void * context);
    static BMA400_INTF_RET_TYPE WriteRegisters(uint8_t reg, const uint8_t * data,
                                             uint32_t length, void * context);
    static void DelayMicroseconds(uint32_t period, void * context);

    bma400_dev mDevice{};
    bool mReady = false;
    int8_t mLastError = BMA400_E_DEV_NOT_FOUND;
};
