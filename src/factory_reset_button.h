// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <cstdint>

// No polling while idle: GPIO edges and one-shot timers drive this state machine.
class FactoryResetButton {
public:
    static constexpr uint32_t kDebounceMs = 30;
    static constexpr uint32_t kHoldMs = 5000;
    static constexpr uint32_t kNoTimer = UINT32_MAX;

    void Initialize(bool pressed)
    {
        // A stuck/boot-held switch must be released before it can reset again.
        mArmed = !pressed;
        mState = State::Idle;
    }

    void Edge(uint64_t nowMs)
    {
        // Every edge cancels the old hold, even if several edges were coalesced.
        mState = State::Debouncing;
        mDeadlineMs = nowMs + kDebounceMs;
    }

    void Cancel()
    {
        mArmed = false;
        mState = State::Idle;
    }

    bool Timer(bool pressed, uint64_t nowMs)
    {
        if (mState == State::Debouncing && nowMs >= mDeadlineMs) {
            if (!pressed) {
                mArmed = true;
                mState = State::Idle;
            } else if (mArmed) {
                mState = State::Holding;
                mDeadlineMs = nowMs + kHoldMs;
            } else {
                mState = State::Idle;
            }
        } else if (mState == State::Holding) {
            // Also check the level at expiry, in case release precedes its ISR.
            if (!pressed) {
                mState = State::Idle;
            } else if (nowMs >= mDeadlineMs) {
                mState = State::Idle;
                mArmed = false;
                return true;
            }
        }
        return false;
    }

    uint32_t NextDelayMs(uint64_t nowMs) const
    {
        if (mState == State::Idle) return kNoTimer;
        return nowMs < mDeadlineMs ? static_cast<uint32_t>(mDeadlineMs - nowMs) : 1;
    }

private:
    enum class State { Idle, Debouncing, Holding };
    State mState = State::Idle;
    bool mArmed = false;
    uint64_t mDeadlineMs = 0;
};
