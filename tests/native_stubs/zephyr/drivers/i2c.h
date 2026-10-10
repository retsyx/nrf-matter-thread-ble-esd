// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <zephyr/device.h>
struct i2c_dt_spec { const device * bus; uint16_t addr; };
extern device test_i2c;
#define DT_NODELABEL(name) 1
#define I2C_DT_SPEC_GET(node) { &test_i2c, TEST_BMA400_ADDRESS }
bool i2c_is_ready_dt(const i2c_dt_spec * spec);
int i2c_write_read_dt(const i2c_dt_spec * spec, const void * write, size_t writeLength,
                      void * read, size_t readLength);
int i2c_write_dt(const i2c_dt_spec * spec, const uint8_t * data, uint32_t length);
