// SPDX-License-Identifier: GPL-3.0-or-later
#include "factory_reset_button.h"
#include <cassert>
#include <cstdio>

int main()
{
    using Button = FactoryResetButton;
    Button button;
    button.Initialize(false);
    assert(button.NextDelayMs(0) == Button::kNoTimer);
    assert(!button.Timer(false, 100000)); // No idle poll can request a reset.

    button.Edge(100000);
    assert(button.NextDelayMs(100000) == Button::kDebounceMs);
    assert(!button.Timer(true, 100029));
    assert(button.NextDelayMs(100029) == 1);
    assert(!button.Timer(true, 100030));
    assert(button.NextDelayMs(100030) == Button::kHoldMs);
    assert(!button.Timer(true, 105029));
    assert(button.Timer(true, 105030)); // Full debounce plus 5 seconds held.
    assert(button.NextDelayMs(105030) == Button::kNoTimer);
    assert(!button.Timer(true, 200000)); // One reset per press.

    // Releasing a short press cancels it. An already queued old timer cannot
    // count toward a new press, even if release and press events coalesce.
    button.Initialize(false);
    button.Edge(0);
    assert(!button.Timer(true, 30));
    button.Edge(4000); // Released before the hold completed.
    assert(!button.Timer(false, 4030));
    assert(button.NextDelayMs(4030) == Button::kNoTimer);
    button.Edge(4500);
    assert(!button.Timer(true, 4530));
    assert(!button.Timer(true, 5030)); // Stale timeout for the first press.
    assert(button.Timer(true, 9530));

    button.Initialize(false);
    button.Edge(0);
    assert(!button.Timer(true, 30));
    button.Edge(5000); // Release/repress coalesced into one observed edge.
    assert(!button.Timer(true, 5030));
    assert(!button.Timer(true, 10029));
    assert(button.Timer(true, 10030));

    // Each bounce restarts debounce, and a late release interrupts the hold.
    button.Initialize(false);
    button.Edge(0);
    button.Edge(20);
    assert(!button.Timer(true, 30));
    button.Edge(40);
    assert(!button.Timer(true, 50));
    assert(!button.Timer(true, 70));
    button.Edge(5069);
    assert(!button.Timer(false, 5070));
    assert(!button.Timer(false, 5099));
    assert(button.NextDelayMs(5099) == Button::kNoTimer);
    assert(!button.Timer(false, 20000));

    // A release sampled at expiry cancels even before its IRQ is handled.
    button.Initialize(false);
    button.Edge(0);
    assert(!button.Timer(true, 30));
    assert(!button.Timer(false, 5030));
    assert(button.NextDelayMs(5030) == Button::kNoTimer);

    // A button held during boot or reboot must be released before arming.
    button.Initialize(true);
    assert(!button.Timer(true, 10000));
    button.Edge(10000); // Noise while still held cannot arm it.
    assert(!button.Timer(true, 10030));
    assert(button.NextDelayMs(10030) == Button::kNoTimer);
    assert(!button.Timer(true, 20000));
    button.Edge(20000);
    assert(!button.Timer(false, 20030));
    button.Edge(21000);
    assert(!button.Timer(true, 21030));
    assert(button.Timer(true, 26030));

    // A GPIO read error cancels safely and also requires release to rearm.
    button.Initialize(false);
    button.Edge(0);
    assert(!button.Timer(true, 30));
    button.Cancel();
    assert(button.NextDelayMs(5000) == Button::kNoTimer);
    button.Edge(6000);
    assert(!button.Timer(true, 6030));
    assert(!button.Timer(true, 11030));
    button.Edge(12000);
    assert(!button.Timer(false, 12030));
    button.Edge(13000);
    assert(!button.Timer(true, 13030));
    assert(button.Timer(true, 18030));

    // Delayed event handling starts the hold at the stable sample, and uptime
    // beyond a 32-bit millisecond counter does not shorten the required hold.
    constexpr uint64_t later = UINT64_C(1) << 33;
    button.Initialize(false);
    button.Edge(later);
    assert(!button.Timer(true, later + 200));
    assert(button.NextDelayMs(later + 200) == Button::kHoldMs);
    assert(!button.Timer(true, later + 5199));
    assert(button.Timer(true, later + 5200));
    std::puts("Factory reset button checks passed");
}
