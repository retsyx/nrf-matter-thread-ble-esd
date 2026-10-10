// SPDX-License-Identifier: GPL-3.0-or-later
#include "battery_monitor.h"

#if defined(CONFIG_MAILBOX_BATTERY_INSTALLED)
#include <zephyr/drivers/adc.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/kernel.h>

namespace {
#define BATTERY_NODE DT_ALIAS(mailbox_battery)
const adc_dt_spec kBatteryAdc = ADC_DT_SPEC_GET(BATTERY_NODE);
constexpr uint32_t kFullOhms = DT_PROP(BATTERY_NODE, full_ohms);
constexpr uint32_t kOutputOhms = DT_PROP(BATTERY_NODE, output_ohms);
constexpr uint32_t kSettlingUs = DT_PROP_OR(BATTERY_NODE, power_on_sample_delay_us, 2000);
constexpr unsigned kSamples = 8;
static_assert(kOutputOhms > 0 && kFullOhms >= kOutputOhms, "Invalid battery divider");

#if DT_NODE_HAS_PROP(BATTERY_NODE, power_gpios)
const gpio_dt_spec kDividerPower = GPIO_DT_SPEC_GET(BATTERY_NODE, power_gpios);

bool SetDividerPower(bool enabled)
{
    return gpio_pin_set_dt(&kDividerPower, enabled ? 1 : 0) == 0;
}
#else
// XIAO nRF52840 keeps the lower divider leg connected with a GPIO hog. Its
// board overlay deliberately has no power-gpios: disconnecting that leg can
// expose the ADC pin to cell voltage above the Nordic absolute maximum.
bool SetDividerPower(bool) { return true; }
#endif
} // namespace
#endif

bool BatteryMonitor::Init()
{
    mReady = false;
#if defined(CONFIG_MAILBOX_BATTERY_INSTALLED)
    if (!adc_is_ready_dt(&kBatteryAdc) || kBatteryAdc.resolution == 0 ||
        kBatteryAdc.resolution > 15 || kBatteryAdc.channel_cfg.differential)
        return false;
#if DT_NODE_HAS_PROP(BATTERY_NODE, power_gpios)
    if (!gpio_is_ready_dt(&kDividerPower) ||
        gpio_pin_configure_dt(&kDividerPower, GPIO_OUTPUT_INACTIVE) != 0)
        return false;
#endif
    mReady = adc_channel_setup_dt(&kBatteryAdc) == 0;
#endif
    return mReady;
}

BatteryMonitor::SampleResult BatteryMonitor::Sample()
{
    SampleResult result;
#if defined(CONFIG_MAILBOX_BATTERY_INSTALLED)
    if (!mReady || !SetDividerPower(true))
        return result;
    // Respect the overlay's active polarity and settling time.
    k_usleep(kSettlingUs);
    int16_t sample = 0;
    adc_sequence sequence{};
    sequence.buffer = &sample;
    sequence.buffer_size = sizeof(sample);
    bool valid = adc_sequence_init_dt(&kBatteryAdc, &sequence) == 0;
    uint32_t sum = 0;
    const uint32_t saturation = (1u << kBatteryAdc.resolution) - 1;
    // Discard the first conversion after switching the analog path.
    for (unsigned i = 0; valid && i <= kSamples; ++i)
    {
        valid = adc_read_dt(&kBatteryAdc, &sequence) == 0 &&
            sample >= 0 && static_cast<uint32_t>(sample) < saturation;
        if (valid && i != 0)
            sum += static_cast<uint16_t>(sample);
    }
    // Always release the switched divider, including failed conversions.
    valid = SetDividerPower(false) && valid;
    if (!valid)
        return result;
    int32_t dividerMv = static_cast<int32_t>((sum + kSamples / 2) / kSamples);
    if (adc_raw_to_millivolts_dt(&kBatteryAdc, &dividerMv) != 0 || dividerMv < 0)
        return result;
    const uint32_t millivolts = static_cast<uint32_t>(
        (static_cast<uint64_t>(dividerMv) * kFullOhms + kOutputOhms / 2) / kOutputOhms);
    if (millivolts < 2200 || millivolts > 4500)
        return result;
    const uint32_t bounded = millivolts < 3500 ? 3500 : (millivolts > 4200 ? 4200 : millivolts);
    const uint8_t percent = static_cast<uint8_t>((bounded - 3500) * 100 / 700);
    result.valid = true;
    result.millivolts = millivolts;
    // Match the reference's coarse voltage estimate; Matter uses 0.5% units.
    result.halfPercent = static_cast<uint8_t>(percent * 2);
    result.low = percent < 20;
#endif
    return result;
}
