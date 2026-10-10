// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <zephyr/device.h>
struct gpio_dt_spec { const device * port; };
extern device test_gpio;
#define GPIO_DT_SPEC_GET(node, property) { &test_gpio }
#define GPIO_OUTPUT_INACTIVE 1
bool gpio_is_ready_dt(const gpio_dt_spec * spec);
int gpio_pin_configure_dt(const gpio_dt_spec * spec, int flags);
int gpio_pin_set_dt(const gpio_dt_spec * spec, int state);
