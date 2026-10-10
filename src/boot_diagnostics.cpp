/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Optional XIAO nRF52840 startup tracing; no Zephyr GPIO device dependency. */
#include "boot_diagnostics.h"
#include <zephyr/init.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <hal/nrf_gpio.h>

LOG_MODULE_REGISTER(boot_diagnostics, LOG_LEVEL_INF);

namespace {
constexpr uint32_t kRedPin = NRF_GPIO_PIN_MAP(0, 26);
constexpr uint32_t kGreenPin = NRF_GPIO_PIN_MAP(0, 30);
constexpr uint32_t kBluePin = NRF_GPIO_PIN_MAP(0, 6);
constexpr uint32_t kRed = 1U;
constexpr uint32_t kGreen = 2U;
constexpr uint32_t kBlue = 4U;

void SetColor(uint32_t color)
{
    /* The module's RGB LED has a common anode: LOW turns a color on. */
    nrf_gpio_pin_write(kRedPin, (color & kRed) ? 0U : 1U);
    nrf_gpio_pin_write(kGreenPin, (color & kGreen) ? 0U : 1U);
    nrf_gpio_pin_write(kBluePin, (color & kBlue) ? 0U : 1U);
}

struct CheckpointPattern {
    uint8_t color;
    uint8_t pulses;
    const char *colorName;
};

constexpr CheckpointPattern kCheckpointPatterns[] = {
    {kRed, 1, "red"}, {kRed, 2, "red"}, {kRed, 3, "red"},
    {kRed, 4, "red"}, {kRed, 5, "red"},
    {kRed | kGreen, 1, "yellow"}, {kRed | kGreen, 2, "yellow"},
    {kRed | kGreen, 3, "yellow"}, {kRed | kGreen, 4, "yellow"},
    {kRed | kBlue, 1, "magenta"}, {kRed | kBlue, 2, "magenta"},
    {kRed | kBlue, 3, "magenta"}, {kRed | kBlue, 4, "magenta"},
    {kBlue, 1, "blue"}, {kBlue, 2, "blue"},
    {kGreen, 1, "green"}, {kGreen, 2, "green"},
};
static_assert(sizeof(kCheckpointPatterns) / sizeof(kCheckpointPatterns[0]) ==
              static_cast<unsigned>(BootDiagnosticStage::StartServerComplete));

uint8_t sCheckpointColor;
uint8_t sCheckpointPulses;
uint8_t sCheckpointTick;
volatile uint32_t sCurrentCheckpoint;

void CheckpointTick(k_timer *)
{
    // 150ms on/off pulses, followed by 600ms dark between groups. The ISR
    // continues identifying a blocked application while the kernel runs.
    const unsigned pulseTicks = 2U * sCheckpointPulses;
    sCheckpointTick = (sCheckpointTick + 1U) % (pulseTicks + 4U);
    SetColor(sCheckpointTick < pulseTicks && !(sCheckpointTick & 1U) ?
             sCheckpointColor : 0U);
}

K_TIMER_DEFINE(sCheckpointTimer, CheckpointTick, nullptr);
} // namespace

void MailboxBootCheckpoint(BootDiagnosticStage stage, const char *description)
{
    const unsigned index = static_cast<unsigned>(stage) - 1U;
    if (index >= sizeof(kCheckpointPatterns) / sizeof(kCheckpointPatterns[0])) return;
    const auto &pattern = kCheckpointPatterns[index];
    // Only called from application initialization, after the scheduler starts.
    // Serialize the state change with the direct GPIO timer callback.
    const unsigned key = irq_lock();
    k_timer_stop(&sCheckpointTimer);
    sCheckpointColor = pattern.color;
    sCheckpointPulses = pattern.pulses;
    sCheckpointTick = 0;
    sCurrentCheckpoint = static_cast<uint32_t>(stage);
    SetColor(pattern.color);
    k_timer_start(&sCheckpointTimer, K_MSEC(150), K_MSEC(150));
    irq_unlock(key);
    LOG_INF("Boot checkpoint %u: %s [%s, %u pulses]", static_cast<unsigned>(stage),
            description, pattern.colorName, static_cast<unsigned>(pattern.pulses));
    // Give deferred USB logging a bounded opportunity to send the last marker.
    k_sleep(K_MSEC(200));
}

uint32_t MailboxBootCurrentStage()
{
    return sCurrentCheckpoint;
}

extern "C" {
static int mailbox_boot_red(void)
{
    /* Set inactive levels before enabling outputs, then mark PRE_KERNEL_1. */
    SetColor(0U);
    nrf_gpio_cfg_output(kRedPin);
    nrf_gpio_cfg_output(kGreenPin);
    nrf_gpio_cfg_output(kBluePin);
    SetColor(kRed);
    return 0;
}

static int mailbox_boot_yellow(void)
{
    SetColor(kRed | kGreen);
    return 0;
}

static int mailbox_boot_magenta(void)
{
    SetColor(kRed | kBlue);
    return 0;
}

static int mailbox_boot_white(void)
{
    SetColor(kRed | kGreen | kBlue);
    return 0;
}

static int mailbox_boot_blue(void)
{
    SetColor(kBlue);
    return 0;
}

static int mailbox_boot_green(void)
{
    SetColor(kGreen);
    return 0;
}

static int mailbox_boot_cyan(void)
{
    SetColor(kGreen | kBlue);
    return 0;
}

static int mailbox_boot_application(void)
{
    /* GNU static constructors and earlier APPLICATION init entries returned. */
    SetColor(0U);
    return 0;
}

/* Priorities intentionally bracket the installed NCS v3.4.1 init entries. */
SYS_INIT(mailbox_boot_red, PRE_KERNEL_1, 0);
SYS_INIT(mailbox_boot_yellow, PRE_KERNEL_1, 41); /* after MPSL priority 40 */
SYS_INIT(mailbox_boot_magenta, PRE_KERNEL_2, 1); /* after RTC priority 0 */
SYS_INIT(mailbox_boot_white, POST_KERNEL, 39); /* before SDC/flash */
SYS_INIT(mailbox_boot_blue, POST_KERNEL, 42); /* after SDC/GPIO hogs */
SYS_INIT(mailbox_boot_green, POST_KERNEL, 51); /* after flash/PSA/USB driver */
SYS_INIT(mailbox_boot_cyan, POST_KERNEL, 91); /* after USB attach priority 90 */
SYS_INIT(mailbox_boot_application, APPLICATION, 99); /* before main() */
} // extern "C"
