// SPDX-License-Identifier: GPL-3.0-or-later
#include "radio_power.h"

#include <zephyr/devicetree.h>

#if DT_NODE_EXISTS(DT_NODELABEL(mailbox_rf_power))
#include <errno.h>
#include <hal/nrf_gpio.h>
#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/regulator.h>
#include <zephyr/init.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/util.h>
#if defined(CONFIG_MAILBOX_RF_SWITCH_POWER_CONTROL)
#include <mpsl_radio_notification.h>
#endif

LOG_MODULE_DECLARE(app, CONFIG_CHIP_APP_LOG_LEVEL);

namespace {
constexpr gpio_dt_spec kRfPower = GPIO_DT_SPEC_GET(DT_NODELABEL(mailbox_rf_power), gpios);
constexpr uint32_t kRfPin = NRF_GPIO_PIN_MAP(2, 3);
// A conservative lead before radio preparation, RX/TX, CCA and ACK handling.
constexpr uint16_t kNotificationLeadUs = 1000;
constexpr int kInitPriority = 46;
BUILD_ASSERT(DT_SAME_NODE(DT_GPIO_CTLR(DT_NODELABEL(mailbox_rf_power), gpios), DT_NODELABEL(gpio2)));
BUILD_ASSERT(DT_GPIO_PIN(DT_NODELABEL(mailbox_rf_power), gpios) == 3);
BUILD_ASSERT(!(DT_GPIO_FLAGS(DT_NODELABEL(mailbox_rf_power), gpios) & GPIO_ACTIVE_LOW));
BUILD_ASSERT(CONFIG_GPIO_INIT_PRIORITY < kInitPriority);
BUILD_ASSERT(CONFIG_REGULATOR_FIXED_INIT_PRIORITY < kInitPriority);
#if defined(CONFIG_MAILBOX_RF_SWITCH_POWER_CONTROL)
BUILD_ASSERT(CONFIG_MPSL_INIT_PRIORITY < kInitPriority);
#endif

struct RadioPowerDiagnostic {
    uint32_t magic;
    uint32_t ready;
    int32_t gpio_error;
    int32_t notification_error;
    uint32_t active_notifications;
    uint32_t inactive_notifications;
    uint32_t power_on;
};
} // namespace

// RAM-only evidence for debugger inspection; no logging or kernel calls in IRQs.
extern "C" {
volatile RadioPowerDiagnostic g_radio_power_diagnostic = {
    0x52465331, 0, -EAGAIN, -ENOTSUP, 0, 0, 0};
}

namespace {
#if defined(CONFIG_MAILBOX_RF_SWITCH_POWER_CONTROL)
void RadioNotification(mpsl_radio_notification_source_t source)
{
    // MPSL calls this at priority zero. Direct OUTSET/OUTCLR writes only:
    // regulator APIs, locks, work queues and logging are unsafe here.
    if (source == MPSL_RADIO_NOTIFICATION_SOURCE_ACTIVE) {
        nrf_gpio_pin_set(kRfPin);
        g_radio_power_diagnostic.power_on = 1;
        ++g_radio_power_diagnostic.active_notifications;
    } else {
        nrf_gpio_pin_clear(kRfPin);
        g_radio_power_diagnostic.power_on = 0;
        ++g_radio_power_diagnostic.inactive_notifications;
    }
}
#endif

int InitRadioPower()
{
    // Fixed regulators initialize at 45, including rfsw_ctl. Leave that
    // active-low regulator enabled: P2.05 must stay low even with RF VDD off.
    const device *select = DEVICE_DT_GET(DT_NODELABEL(rfsw_ctl));
    if (!gpio_is_ready_dt(&kRfPower) || !device_is_ready(select) || !regulator_is_enabled(select)) {
        g_radio_power_diagnostic.gpio_error = -ENODEV;
        return -ENODEV;
    }
    const int error = gpio_pin_configure_dt(&kRfPower, GPIO_OUTPUT_ACTIVE);
    g_radio_power_diagnostic.gpio_error = error;
    if (error != 0) return error;
    g_radio_power_diagnostic.power_on = 1;

#if defined(CONFIG_MAILBOX_RF_SWITCH_POWER_CONTROL)
    // MPSL is ready; protocol stacks have not started. Register here rather
    // than in main(), after the Thread driver's initialization has run.
    const int notification_error = mpsl_radio_notification_cfg_set(
        MPSL_RADIO_NOTIFICATION_TYPE_INT_ON_BOTH, kNotificationLeadUs, RadioNotification);
    g_radio_power_diagnostic.notification_error = notification_error;
    // Keep VDD high until the first INACTIVE callback. A missing notification
    // stream therefore leaves the radio powered. Subsequent ACTIVE callbacks
    // restore VDD before preparation; coalesced events keep it on until the
    // final INACTIVE, covering both receive and transmit.
    // Registration failure also retains GPIO_OUTPUT_ACTIVE.
#endif
    g_radio_power_diagnostic.ready = 1;
    return 0;
}

// After GPIO/fixed regulators and MPSL, before Thread initialization and main.
SYS_INIT(InitRadioPower, POST_KERNEL, 46);
} // namespace

int CheckRadioPowerInitialization()
{
    if (!g_radio_power_diagnostic.ready) {
        LOG_ERR("RF switch GPIO initialization failed: %d", g_radio_power_diagnostic.gpio_error);
        return g_radio_power_diagnostic.gpio_error;
    }
#if defined(CONFIG_MAILBOX_RF_SWITCH_POWER_CONTROL)
    if (g_radio_power_diagnostic.notification_error == 0) {
        LOG_INF("RF switch follows radio activity, lead=%u us", kNotificationLeadUs);
    } else {
        LOG_WRN("RF notifications unavailable (%d); keeping antenna switch powered",
                g_radio_power_diagnostic.notification_error);
    }
#else
    LOG_INF("RF switch continuously powered");
#endif
    return 0;
}

#else
int CheckRadioPowerInitialization() { return 0; }
#endif
