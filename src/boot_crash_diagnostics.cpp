// SPDX-License-Identifier: GPL-3.0-or-later
#include "boot_crash_diagnostics.h"
#include "boot_diagnostics.h"

#include <zephyr/fatal.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/shell/shell.h>
#include <zephyr/sys/reboot.h>
#include <cmsis_core.h>
#include <nrfx.h>
#include <stdlib.h>

LOG_MODULE_REGISTER(boot_crash, LOG_LEVEL_INF);

namespace {
// Recorder translation-unit compilation tag helps reject stale records. It is
// not a fingerprint of the full firmware during incremental app-only builds.
constexpr uint32_t kImageTag = MailboxCrash::ImageTag(__DATE__ " " __TIME__);

// Dedicated storage, never a reused stack/buffer address. Zephyr does not clear
// it on warm reboot. Adafruit0.6.1 uses RAM20008000..20040000 too, so bootloader
// survival is best effort until observed; GPREGRET2 provides a separate marker.
__attribute__((section(".noinit.mailbox_crash"))) volatile MailboxCrash::Record g_record;
volatile uint32_t g_abortCaller;
volatile uint32_t g_abortStackPointer;
volatile uint32_t g_abortStackWordCount;
volatile uint32_t g_abortStack[MailboxCrash::StackWords];

int EnterUf2(const struct shell *shell, size_t argc, char **argv)
{
    ARG_UNUSED(shell);
    ARG_UNUSED(argc);
    ARG_UNUSED(argv);
    // Stock0.6.1 recognizes0x57 as CDC+MSC UF2 recovery. No flash is erased.
    g_record.magic = 0;
    NRF_POWER->GPREGRET2 = 0;
    NRF_POWER->GPREGRET = 0x57;
    __DSB();
    NVIC_SystemReset();
    return 0;
}
} // namespace

SHELL_CMD_REGISTER(mailbox_uf2, NULL, "Reboot into stock USB UF2 recovery", EnterUf2);

// The diagnostic linker wraps abort because Zephyr's own strong implementation
// is included with --whole-archive. Capture before its printk destroys LR.
extern "C" FUNC_NORETURN __attribute__((noinline)) void __wrap_abort(void)
{
    g_abortCaller = static_cast<uint32_t>(reinterpret_cast<uintptr_t>(
        __builtin_extract_return_addr(__builtin_return_address(0))));
    uint32_t stackPointer;
    __asm__ volatile("mov %0, sp" : "=r"(stackPointer));
    g_abortStackPointer = stackPointer;
    g_abortStackWordCount = 0;
    // Read upward from this function's actual SP, including its saved caller
    // frame. Check alignment and physical RAM bounds before each bounded copy.
    if ((stackPointer & 3U) == 0U && stackPointer >= 0x20000000U &&
        stackPointer < 0x20040000U) {
        uint32_t count = (0x20040000U - stackPointer) / sizeof(uint32_t);
        if (count > MailboxCrash::StackWords) {
            count = MailboxCrash::StackWords;
        }
        const volatile uint32_t *stack = reinterpret_cast<const volatile uint32_t *>(stackPointer);
        for (uint32_t i = 0; i < count; ++i) {
            g_abortStack[i] = stack[i];
        }
        g_abortStackWordCount = count;
    }
    k_panic();
    CODE_UNREACHABLE;
}

// A strong diagnostic-only policy replaces the weak Zephyr handler. Do not
// LOG_PANIC/print/allocate or return to the damaged kernel while IRQs are locked.
extern "C" void k_sys_fatal_error_handler(unsigned int reason, const struct arch_esf *esf)
{
    g_record.magic = 0;
    g_record.version = MailboxCrash::Version;
    g_record.imageTag = kImageTag;
    g_record.bytes = sizeof(g_record);
    g_record.reason = reason;
    g_record.stage = MailboxBootCurrentStage();
    g_record.frameValid = esf != nullptr;
    g_record.pc = esf ? esf->basic.pc : 0;
    g_record.lr = esf ? esf->basic.lr : 0;
    g_record.xpsr = esf ? esf->basic.xpsr : 0;
    g_record.r0 = esf ? esf->basic.r0 : 0;
    g_record.r1 = esf ? esf->basic.r1 : 0;
    g_record.r2 = esf ? esf->basic.r2 : 0;
    g_record.r3 = esf ? esf->basic.r3 : 0;
    g_record.r12 = esf ? esf->basic.r12 : 0;
    // The architecture decoder may have acknowledged fault status already;
    // these are the register values available at the policy hook, not raw entry.
    g_record.cfsr = SCB->CFSR;
    g_record.hfsr = SCB->HFSR;
    g_record.dfsr = SCB->DFSR;
    g_record.afsr = SCB->AFSR;
    g_record.mmfar = SCB->MMFAR;
    g_record.bfar = SCB->BFAR;
    g_record.icsr = SCB->ICSR;
    g_record.thread = static_cast<uint32_t>(reinterpret_cast<uintptr_t>(k_current_get()));
    g_record.abortCaller = g_abortCaller;
    g_record.abortStackPointer = g_abortStackPointer;
    g_record.abortStackWordCount = g_abortStackWordCount;
    for (uint32_t i = 0; i < MailboxCrash::StackWords; ++i) {
        g_record.abortStack[i] = g_abortStack[i];
    }
    g_record.checksum = MailboxCrash::Checksum(g_record);
    __DSB();
    g_record.magic = MailboxCrash::Magic; // commit last
    NRF_POWER->GPREGRET2 = MailboxCrash::Marker(reason, g_record.stage);
    NRF_POWER->GPREGRET = 0; // normal app restart; recovery command uses0x57
    __DSB();
    sys_reboot(SYS_REBOOT_WARM);
}

void MailboxBootCrashReportAndIdle()
{
    const uint32_t marker = NRF_POWER->GPREGRET2 & 0xffU;
    const bool valid = MailboxCrash::Valid(g_record, kImageTag);
    const bool markerValid = MailboxCrash::MarkerValid(marker);
    const uint32_t bucket = marker >> 5;
    const int markerReason = bucket <= 5U ? static_cast<int>(bucket) - 1 : -1;
    NRF_POWER->GPREGRET2 = 0;
    if (!valid && !markerValid) {
        g_record.magic = 0;
        return;
    }

    // Keep a snapshot after consuming the persistent record, so a later manual
    // reboot can retry rather than being permanently trapped in diagnostic idle.
    MailboxCrash::Record record;
    // Byte access copies volatile storage without aliasing unrelated word types.
    const volatile unsigned char *source = reinterpret_cast<const volatile unsigned char *>(&g_record);
    unsigned char *destination = reinterpret_cast<unsigned char *>(&record);
    for (size_t i = 0; i < sizeof(record); ++i) {
        destination[i] = source[i];
    }
    g_record.magic = 0;

    for (;;) {
        if (valid) {
            LOG_ERR("Retained fatal: reason=%u stage=%u thread=0x%08x frame=%u",
                    record.reason, record.stage, record.thread, record.frameValid);
            LOG_ERR("PC=0x%08x LR=0x%08x xPSR=0x%08x", record.pc, record.lr, record.xpsr);
            LOG_ERR("abort caller=0x%08x SP=0x%08x words=%u",
                    record.abortCaller, record.abortStackPointer, record.abortStackWordCount);
            for (uint32_t i = 0; i < record.abortStackWordCount; i += 4U) {
                LOG_ERR("abort stack[%02u]: %08x %08x %08x %08x", i,
                        record.abortStack[i], record.abortStack[i + 1U],
                        record.abortStack[i + 2U], record.abortStack[i + 3U]);
            }
            LOG_ERR("r0=%08x r1=%08x r2=%08x r3=%08x r12=%08x",
                    record.r0, record.r1, record.r2, record.r3, record.r12);
            LOG_ERR("Hook fault regs: CFSR=%08x HFSR=%08x DFSR=%08x AFSR=%08x",
                    record.cfsr, record.hfsr, record.dfsr, record.afsr);
            LOG_ERR("MMFAR=%08x BFAR=%08x ICSR=%08x", record.mmfar, record.bfar, record.icsr);
        } else {
            LOG_ERR("No valid RAM crash record; GPREGRET2=0x%02x reason=%d stage=%u (other=-1)",
                    marker, markerReason, marker & 31U);
        }
        LOG_INF("Diagnostic idle; use mailbox_uf2 to enter USB recovery");
        k_sleep(K_SECONDS(3));
    }
}
