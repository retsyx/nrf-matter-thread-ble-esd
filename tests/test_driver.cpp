// SPDX-License-Identifier: GPL-3.0-or-later
// Real SensorAPI and ported adapters; only Zephyr bus/kernel services are mocked.
#include "Bma400Motion.h"
#include "battery_monitor.h"
#include <zephyr/drivers/i2c.h>
#include <zephyr/drivers/adc.h>
#include <zephyr/drivers/gpio.h>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <initializer_list>

device test_i2c{true};
device test_adc{true};
device test_gpio{true};
namespace {
uint8_t registers[256]{};
bool nack = false;
bool wrongChip = false;
unsigned transfers = 0;
unsigned failAtTransfer = 0;
unsigned fifoDisableWriteTransfer = 0;
unsigned delays = 0;
unsigned adcReads = 0;
unsigned adcFailAt = 0;
int16_t adcRaw = 1900;
bool adcSetupFails = false;
bool adcConvertFails = false;
bool dividerEnabled = false;
unsigned gpioSets = 0;

void ResetRegisters()
{
    std::memset(registers, 0, sizeof(registers));
    registers[0] = wrongChip ? 0 : 0x90;
    registers[0x1A] = 0x49; // +/-4 g, normal ODR 200 Hz, reset defaults.
}
void CheckConfiguration()
{
    assert(registers[0x19] == 0x01); // Low-power mode, OSR0.
    assert(registers[0x1A] == 0x49);
    assert(registers[0x2F] == 0xFD); // XYZ, 8 samples, one-time reference.
    assert(registers[0x30] == 4);
    assert(registers[0x31] == 255 && registers[0x32] == 255 && registers[0x33] == 255);
    assert(registers[0x24] == 0x02); // Active-high push-pull INT1.
    assert(registers[0x21] == 0x01); // Wake-up interrupt remains mapped to INT1.
    assert(registers[0x29] == 0x01); // Unused FIFO read circuit disabled.
    assert(registers[0x2A] == 0 && registers[0x2B] == 0); // Auto-LP timer unused.
    assert(registers[0x2D] == 0); // Motion must not switch to normal mode.
    assert(registers[0x1F] == 0 && registers[0x20] == 0); // GEN2 off, nonlatching.
}
bool BeginTransfer(const i2c_dt_spec * spec)
{
    assert(spec->bus == &test_i2c && spec->addr == TEST_BMA400_ADDRESS);
    ++transfers;
    return !nack && (failAtTransfer == 0 || transfers != failAtTransfer);
}
} // namespace

int32_t k_usleep(int32_t period)
{
    assert(period > 0);
    ++delays;
    return 0;
}
bool i2c_is_ready_dt(const i2c_dt_spec * spec) { return spec->bus->ready; }
int i2c_write_read_dt(const i2c_dt_spec * spec, const void * write, size_t writeLength,
                      void * read, size_t readLength)
{
    assert(writeLength == 1 && readLength != 0);
    if (!BeginTransfer(spec)) return -1;
    const uint8_t reg = *static_cast<const uint8_t *>(write);
    assert(reg + readLength <= sizeof(registers));
    std::memcpy(read, registers + reg, readLength);
    return 0;
}
int i2c_write_dt(const i2c_dt_spec * spec, const uint8_t * data, uint32_t length)
{
    assert(length == 2); // Register and one data byte in the same transaction.
    if (!BeginTransfer(spec)) return -1;
    if (data[0] == 0x29 && data[1] == 0x01)
        fifoDisableWriteTransfer = transfers;
    if (data[0] == 0x7E && data[1] == 0xB6)
        ResetRegisters();
    else
        registers[data[0]] = data[1];
    return 0;
}
bool adc_is_ready_dt(const adc_dt_spec * spec) { return spec->dev->ready; }
int adc_channel_setup_dt(const adc_dt_spec *) { return adcSetupFails ? -1 : 0; }
int adc_sequence_init_dt(const adc_dt_spec *, adc_sequence *) { return 0; }
int adc_read_dt(const adc_dt_spec *, const adc_sequence * sequence)
{
    assert(sequence->buffer_size == sizeof(int16_t));
#if TEST_BATTERY_SWITCHED
    assert(dividerEnabled);
#endif
    ++adcReads;
    if (adcFailAt != 0 && adcReads == adcFailAt) return -1;
    *static_cast<int16_t *>(sequence->buffer) = adcRaw;
    return 0;
}
int adc_raw_to_millivolts_dt(const adc_dt_spec *, int32_t *)
{
    // Conversion is mocked 1 raw count = 1 mV at the divider output.
    return adcConvertFails ? -1 : 0;
}
bool gpio_is_ready_dt(const gpio_dt_spec * spec) { return spec->port->ready; }
int gpio_pin_configure_dt(const gpio_dt_spec *, int flags)
{
    assert(flags == GPIO_OUTPUT_INACTIVE);
    dividerEnabled = false;
    return 0;
}
int gpio_pin_set_dt(const gpio_dt_spec *, int state)
{
    dividerEnabled = state != 0;
    ++gpioSets;
    return 0;
}

void TestBma400()
{
    Bma400Motion sensor;
    uint16_t status = 123;
    assert(sensor.ReadInterruptStatus(status) == BMA400_E_DEV_NOT_FOUND && status == 0);
    test_i2c.ready = false;
    assert(sensor.Init() == BMA400_E_COM_FAIL && transfers == 0);
    test_i2c.ready = true;
    nack = true;
    assert(sensor.Init() == BMA400_E_COM_FAIL && !sensor.IsReady() && transfers == 1);
    nack = false;
    transfers = 0;
    assert(sensor.Init() == BMA400_OK && sensor.IsReady() && sensor.ChipId() == 0x90);
    assert(sensor.LastError() == BMA400_OK && delays != 0);
    CheckConfiguration();
    const unsigned successfulInitTransfers = transfers;
    const unsigned fifoDisableTransfer = fifoDisableWriteTransfer;
    assert(fifoDisableTransfer > 0 && fifoDisableTransfer <= successfulInitTransfers);
    // Abort every initialization transfer failure, including the FIFO power write,
    // without hidden transport retries or reporting a partially configured sensor ready.
    for (unsigned failure = 1; failure <= successfulInitTransfers; ++failure)
    {
        transfers = 0;
        failAtTransfer = failure;
        assert(sensor.Init() == BMA400_E_COM_FAIL);
        assert(!sensor.IsReady() && transfers == failure);
        if (failure == fifoDisableTransfer)
            assert(registers[0x29] == 0); // Failed write leaves the reset default.
    }
    failAtTransfer = 0;
    // Rearming resets the device and must restore the low-power configuration.
    registers[0x19] = 2;
    registers[0x21] = 0;
    registers[0x29] = 0;
    registers[0x2A] = 25;
    registers[0x2B] = 8;
    registers[0x2D] = 2;
    registers[0x1F] = 8;
    assert(sensor.Init() == BMA400_OK);
    CheckConfiguration();
    registers[0x0E] = 1;
    const unsigned delaysBeforeStatus = delays;
    assert(sensor.ReadInterruptStatus(status) == BMA400_OK && status == BMA400_ASSERTED_WAKEUP_INT);
    assert(delays == delaysBeforeStatus);
    const uint8_t acceleration[] = { 0xFF, 0x07, 0x00, 0x08, 0xFF, 0x0F };
    std::memcpy(registers + 0x04, acceleration, sizeof(acceleration));
    bma400_sensor_data sample{};
    assert(sensor.ReadSample(sample) == BMA400_OK);
    assert(sample.x == 2047 && sample.y == -2048 && sample.z == -1);
    nack = true;
    status = 123;
    const unsigned beforeFailure = transfers;
    assert(sensor.ReadInterruptStatus(status) == BMA400_E_COM_FAIL && status == 0);
    assert(sensor.LastError() == BMA400_E_COM_FAIL && transfers == beforeFailure + 1);
    assert(sensor.ReadSample(sample) == BMA400_E_COM_FAIL);
    assert(sample.x == 0 && sample.y == 0 && sample.z == 0);
    nack = false;
    wrongChip = true;
    assert(sensor.Init() == BMA400_E_DEV_NOT_FOUND && !sensor.IsReady());
    wrongChip = false;
    assert(sensor.Init() == BMA400_OK);
    CheckConfiguration();
}

void TestBattery()
{
    BatteryMonitor battery;
    assert(!battery.Sample().valid);
#if defined(CONFIG_MAILBOX_BATTERY_INSTALLED)
    test_adc.ready = false;
    assert(!battery.Init());
    test_adc.ready = true;
    adcSetupFails = true;
    assert(!battery.Init());
    adcSetupFails = false;
    assert(battery.Init());
    auto sample = battery.Sample();
    assert(sample.valid && sample.millivolts == 3800 && sample.halfPercent == 84 && !sample.low);
    assert(adcReads == 9 && !dividerEnabled); // Discard + eight samples, safe cleanup.
    adcRaw = 1780;
    sample = battery.Sample();
    assert(sample.valid && sample.halfPercent == 16 && sample.low);
    adcRaw = 2200;
    sample = battery.Sample();
    assert(sample.valid && sample.millivolts == 4400 && sample.halfPercent == 200);
    // Invalid/out-of-range inputs and read failures never publish a valid result.
    for (int16_t invalid : { static_cast<int16_t>(-1), static_cast<int16_t>(4095),
                             static_cast<int16_t>(1000), static_cast<int16_t>(2300) })
    {
        adcRaw = invalid;
        assert(!battery.Sample().valid && !dividerEnabled);
    }
    adcRaw = 1900;
    for (unsigned failure = 1; failure <= 9; ++failure)
    {
        adcReads = 0;
        adcFailAt = failure;
        assert(!battery.Sample().valid && !dividerEnabled);
    }
    adcFailAt = 0;
    adcConvertFails = true;
    assert(!battery.Sample().valid && !dividerEnabled);
    adcConvertFails = false;
    assert(battery.Sample().valid);
#if TEST_BATTERY_SWITCHED
    assert(gpioSets > 0);
#else
    assert(gpioSets == 0); // nRF52840 lower divider leg stays connected.
#endif
#else
    assert(!battery.Init() && !battery.Sample().valid && adcReads == 0 && gpioSets == 0);
#endif
}

int main()
{
    TestBma400();
    TestBattery();
    std::puts("BMA400 and battery Zephyr transport regression checks passed");
}
