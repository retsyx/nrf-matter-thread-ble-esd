// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include <stdint.h>

// Application checkpoints supplement the early SYS_INIT colors. Each value has
// a distinct repeating color/pulse-count pattern in the USB diagnostic profile.
enum class BootDiagnosticStage : uint8_t {
    BoardPowerBegin = 1,
    FlashGpioReady,
    FlashGpioConfigured,
    FlashCommandComplete,
    BoardPowerComplete,
    MotionGpioBegin,
    MotionGpioComplete,
    MotionCallbackBegin,
    MotionCallbackComplete,
    TimersBegin,
    TimersComplete,
    BatteryBegin,
    BatteryComplete,
    PrepareServerBegin,
    PrepareServerComplete,
    StartServerBegin,
    StartServerComplete,
};

#if defined(CONFIG_MAILBOX_BOOT_DIAGNOSTICS)
void MailboxBootCheckpoint(BootDiagnosticStage stage, const char *description);
// Safe in a fatal handler: a single scalar read, with zero before AppTask::Init.
uint32_t MailboxBootCurrentStage();
#else
// Discard descriptions and stage arguments entirely in the production build.
#define MailboxBootCheckpoint(stage, description) do { } while (0)
#endif
