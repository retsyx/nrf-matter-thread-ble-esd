// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include <stdint.h>

namespace MailboxCrash {
constexpr uint32_t Magic = 0x4d424352; // MBCR
constexpr uint32_t Version = 2;
constexpr uint32_t StackWords = 32;

struct Record {
    uint32_t magic, version, imageTag, bytes;
    uint32_t reason, stage, frameValid, pc, lr, xpsr;
    uint32_t r0, r1, r2, r3, r12;
    uint32_t cfsr, hfsr, dfsr, afsr, mmfar, bfar, icsr;
    uint32_t thread, abortCaller, abortStackPointer, abortStackWordCount;
    uint32_t abortStack[StackWords];
    uint32_t checksum;
};

constexpr uint32_t ImageTag(const char *text)
{
    uint32_t value = 2166136261U;
    while (*text != '\0') {
        value = (value ^ static_cast<uint8_t>(*text++)) * 16777619U;
    }
    return value;
}

inline uint32_t Checksum(const volatile Record &record)
{
    const volatile uint32_t *fields[] = {
        &record.version, &record.imageTag, &record.bytes, &record.reason,
        &record.stage, &record.frameValid, &record.pc, &record.lr, &record.xpsr,
        &record.r0, &record.r1, &record.r2, &record.r3, &record.r12,
        &record.cfsr, &record.hfsr, &record.dfsr, &record.afsr,
        &record.mmfar, &record.bfar, &record.icsr, &record.thread,
        &record.abortCaller, &record.abortStackPointer, &record.abortStackWordCount,
    };
    uint32_t value = 2166136261U;
    for (const volatile uint32_t *field : fields) {
        value = (value ^ *field) * 16777619U;
    }
    for (uint32_t i = 0; i < StackWords; ++i) {
        value = (value ^ record.abortStack[i]) * 16777619U;
    }
    return value;
}

inline bool Valid(const volatile Record &record, uint32_t imageTag)
{
    return record.magic == Magic && record.version == Version &&
        record.imageTag == imageTag && record.bytes == sizeof(Record) &&
        record.frameValid <= 1U && record.abortStackWordCount <= StackWords &&
        record.checksum == Checksum(record);
}

// Buckets1..5 encode generic Zephyr reasons0..4; bucket7 means an architecture
// specific/other reason. Exact reason and registers remain in the RAM record.
inline uint8_t Marker(uint32_t reason, uint32_t stage)
{
    const uint32_t bucket = reason <= 4U ? reason + 1U : 7U;
    return static_cast<uint8_t>((bucket << 5) | (stage <= 17U ? stage : 0U));
}

inline bool MarkerValid(uint32_t raw)
{
    const uint32_t bucket = raw >> 5;
    return raw <= 0xffU && (raw & 31U) <= 17U &&
        ((bucket >= 1U && bucket <= 5U) || bucket == 7U);
}
} // namespace MailboxCrash

#if defined(CONFIG_MAILBOX_BOOT_DIAGNOSTICS)
// Returns when no valid crash remains; otherwise reports repeatedly and sleeps
// without entering AppTask again, keeping USB and the recovery shell available.
void MailboxBootCrashReportAndIdle();
#endif
