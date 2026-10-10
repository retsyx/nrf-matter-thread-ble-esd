// SPDX-License-Identifier: GPL-3.0-or-later
#include "board_power.h"
#include "boot_diagnostics.h"
#include "radio_power.h"

#include <errno.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/kernel.h>

#if DT_NODE_EXISTS(DT_NODELABEL(mailbox_flash_sleep))
namespace {
#define FLASH_SLEEP_NODE DT_NODELABEL(mailbox_flash_sleep)
const gpio_dt_spec kCs = GPIO_DT_SPEC_GET(FLASH_SLEEP_NODE, cs_gpios);
const gpio_dt_spec kSck = GPIO_DT_SPEC_GET(FLASH_SLEEP_NODE, sck_gpios);
const gpio_dt_spec kMosi = GPIO_DT_SPEC_GET(FLASH_SLEEP_NODE, mosi_gpios);
const gpio_dt_spec kMiso = GPIO_DT_SPEC_GET(FLASH_SLEEP_NODE, miso_gpios);
const gpio_dt_spec kWp = GPIO_DT_SPEC_GET(FLASH_SLEEP_NODE, wp_gpios);
const gpio_dt_spec kHold = GPIO_DT_SPEC_GET(FLASH_SLEEP_NODE, hold_gpios);

int SendDeepPowerDown()
{
    // P25Q16H datasheet sections 5.3 and 10.30: SPI mode 0, MSB first,
    // command B9h; no write-enable or address. GPIO clocks avoid bringing
    // up a QSPI driver solely for an unused device. Both QSPI and SPI are
    // disabled by the board overlay, so these pins have no concurrent owner.
    constexpr unsigned command = 0xb9;
    int ret = gpio_pin_set_dt(&kCs, 1);
    if (ret < 0) return ret;
    for (unsigned mask = 0x80; mask; mask >>= 1) {
        ret = gpio_pin_set_dt(&kMosi, (command & mask) != 0);
        if (ret < 0) return ret;
        k_busy_wait(1);
        ret = gpio_pin_set_dt(&kSck, 1);
        if (ret < 0) return ret;
        k_busy_wait(1);
        ret = gpio_pin_set_dt(&kSck, 0);
        if (ret < 0) return ret;
    }
    k_busy_wait(1);
    return 0;
}
} // namespace
#endif

int InitBoardPower()
{
#if DT_NODE_EXISTS(DT_NODELABEL(mailbox_flash_sleep))
    const gpio_dt_spec *const pins[] = { &kCs, &kSck, &kMosi, &kMiso, &kWp, &kHold };
    for (const auto *pin : pins) {
        if (!gpio_is_ready_dt(pin)) return -ENODEV;
    }
    MailboxBootCheckpoint(BootDiagnosticStage::FlashGpioReady, "flash GPIO devices ready, before pin configuration");
    const gpio_flags_t flags[] = {
        GPIO_OUTPUT_INACTIVE, GPIO_OUTPUT_INACTIVE, GPIO_OUTPUT_INACTIVE,
        GPIO_DISCONNECTED, GPIO_OUTPUT_ACTIVE, GPIO_OUTPUT_ACTIVE
    };
    for (unsigned i = 0; i < sizeof(pins) / sizeof(pins[0]); ++i) {
        const int ret = gpio_pin_configure_dt(pins[i], flags[i]);
        if (ret < 0) return ret;
    }
    MailboxBootCheckpoint(BootDiagnosticStage::FlashGpioConfigured, "flash pins configured, before deep-power-down command");
    // CS high since reset, WP/HOLD high. 150 us exceeds tVSL (70 us).
    k_busy_wait(150);
    const int ret = SendDeepPowerDown();
    const int release = gpio_pin_set_dt(&kCs, 0);
    // P25Q16H tDP max is 3 us after CS rises. Keep all outputs at CMOS
    // levels. MISO remains an input with its input buffer disconnected and
    // no pull: the sleeping flash leaves it floating, and it is never read.
    k_busy_wait(10);
    MailboxBootCheckpoint(BootDiagnosticStage::FlashCommandComplete, "flash deep-power-down command and CS release returned");
    return ret < 0 ? ret : release;
#else
    // nRF54L15 has no external flash. RF power is initialized before the
    // radio stacks; GPIO hogs initialize unused supplies, LED and divider.
    return CheckRadioPowerInitialization();
#endif
}
