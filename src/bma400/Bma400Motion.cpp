// SPDX-License-Identifier: GPL-3.0-or-later
// Nordic/Zephyr transport for the mailbox sensor's Bosch BMA400 SensorAPI.
#include "Bma400Motion.h"

#include <zephyr/device.h>
#include <zephyr/drivers/i2c.h>
#include <zephyr/kernel.h>
#include <cstring>

namespace {
const i2c_dt_spec kSensor = I2C_DT_SPEC_GET(DT_NODELABEL(mailbox_bma400));
} // namespace

int8_t Bma400Motion::Init()
{
    mReady = false;
    mDevice = {};
    if (!i2c_is_ready_dt(&kSensor))
        return mLastError = BMA400_E_COM_FAIL;
    if (kSensor.addr != BMA400_I2C_ADDRESS_SDO_LOW && kSensor.addr != BMA400_I2C_ADDRESS_SDO_HIGH)
        return mLastError = BMA400_E_INVALID_CONFIG;

    mDevice.intf = BMA400_I2C_INTF;
    mDevice.intf_ptr = this;
    mDevice.read = ReadRegisters;
    mDevice.write = WriteRegisters;
    mDevice.delay_us = DelayMicroseconds;
    mLastError = bma400_soft_reset(&mDevice);
    if (mLastError == BMA400_OK)
        mLastError = bma400_init(&mDevice);
    if (mLastError == BMA400_OK)
        mLastError = bma400_set_power_mode(BMA400_MODE_NORMAL, &mDevice);
    if (mLastError == BMA400_OK)
        mLastError = ConfigureMotion();
    mReady = mLastError == BMA400_OK;
    return mLastError;
}

int8_t Bma400Motion::ConfigureMotion()
{
    // Reset defaults provide low-power OSR0, fixed 25 Hz low-power sampling,
    // and a +/-4 g range. Normal-mode sampling/filter defaults are preserved.
    bma400_device_conf wakeup = {};
    wakeup.type = BMA400_AUTOWAKEUP_INT;
    wakeup.param.wakeup.wakeup_ref_update = BMA400_UPDATE_ONE_TIME;
    wakeup.param.wakeup.sample_count = BMA400_SAMPLE_COUNT_8;
    wakeup.param.wakeup.wakeup_axes_en = BMA400_AXIS_XYZ_EN;
    wakeup.param.wakeup.int_wkup_threshold = 4;
    wakeup.param.wakeup.int_wkup_ref_x = 255;
    wakeup.param.wakeup.int_wkup_ref_y = 255;
    wakeup.param.wakeup.int_wkup_ref_z = 255;
    wakeup.param.wakeup.int_chan = BMA400_INT_CHANNEL_1;
    int8_t status = bma400_set_device_conf(&wakeup, 1, &mDevice);
    if (status != BMA400_OK)
    {
        return status;
    }

    bma400_device_conf pin = {};
    pin.type = BMA400_INT_PIN_CONF;
    pin.param.int_conf.int_chan = BMA400_INT_CHANNEL_1;
    pin.param.int_conf.pin_conf = BMA400_INT_PUSH_PULL_ACTIVE_1;
    status = bma400_set_device_conf(&pin, 1, &mDevice);
    if (status != BMA400_OK)
    {
        return status;
    }

    // The wake-up detector always runs in low-power mode and can drive INT1
    // independently of auto-wakeup (Bosch datasheet, p. 21). Notify the
    // MCU while keeping the BMA400 in low power. The application's five-second
    // rearm calls Init() again, capturing a fresh one-time motion reference.
    bma400_int_enable interrupts[2] = {};
    interrupts[0].type = BMA400_LATCH_INT_EN;
    interrupts[0].conf = BMA400_DISABLE;
    interrupts[1].type = BMA400_AUTO_WAKEUP_EN;
    interrupts[1].conf = BMA400_DISABLE;
    status = bma400_enable_interrupt(interrupts, 2, &mDevice);
    if (status != BMA400_OK)
    {
        return status;
    }

    // FIFO_PWR_CONFIG.fifo_read_disable saves 100 nA. No FIFO data is used by
    // this application. Apply after every reset; all other bits are reserved.
    const uint8_t fifoReadDisable = 0x01;
    status = bma400_set_regs(BMA400_REG_FIFO_READ_EN, &fifoReadDisable, 1, &mDevice);
    if (status != BMA400_OK)
    {
        return status;
    }
    return bma400_set_power_mode(BMA400_MODE_LOW_POWER, &mDevice);
}

int8_t Bma400Motion::ReadInterruptStatus(uint16_t & status)
{
    status = 0;
    if (!mReady)
        return mLastError = BMA400_E_DEV_NOT_FOUND;
    return mLastError = bma400_get_interrupt_status(&status, &mDevice);
}

int8_t Bma400Motion::ReadSample(bma400_sensor_data & sample)
{
    sample = {};
    if (!mReady)
        return mLastError = BMA400_E_DEV_NOT_FOUND;
    return mLastError = bma400_get_accel_data(BMA400_DATA_ONLY, &sample, &mDevice);
}

BMA400_INTF_RET_TYPE Bma400Motion::ReadRegisters(uint8_t reg, uint8_t * data,
                                               uint32_t length, void * context)
{
    if (context == nullptr || data == nullptr || length == 0 || length > UINT16_MAX)
        return BMA400_E_COM_FAIL;
    // The combined transaction writes the register then issues a repeated-start
    // read. SensorAPI owns dummy-byte removal (zero dummy bytes for I2C).
    return i2c_write_read_dt(&kSensor, &reg, 1, data, length) == 0
        ? BMA400_INTF_RET_SUCCESS : BMA400_E_COM_FAIL;
}

BMA400_INTF_RET_TYPE Bma400Motion::WriteRegisters(uint8_t reg, const uint8_t * data,
                                                uint32_t length, void * context)
{
    if (context == nullptr || data == nullptr || length == 0 || length > 256)
        return BMA400_E_COM_FAIL;
    // Use one contiguous RAM buffer: TWIM then needs no optional concatenation
    // buffer, and the register and data stay in the same bus transaction.
    // SensorAPI v1.5.8 calls this once per byte for the I2C interface.
    uint8_t bytes[257];
    bytes[0] = reg;
    std::memcpy(bytes + 1, data, length);
    return i2c_write_dt(&kSensor, bytes, length + 1) == 0
        ? BMA400_INTF_RET_SUCCESS : BMA400_E_COM_FAIL;
}

void Bma400Motion::DelayMicroseconds(uint32_t period, void *)
{
    // Zephyr sleeps release the CPU during Bosch's reset/startup delays.
    if (period != 0)
        k_usleep(period);
}
