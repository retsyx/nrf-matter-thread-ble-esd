// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <zephyr/device.h>
struct adc_channel_cfg { bool differential; };
struct adc_dt_spec { const device * dev; adc_channel_cfg channel_cfg; uint8_t resolution; };
struct adc_sequence { void * buffer; size_t buffer_size; };
extern device test_adc;
#define DT_ALIAS(name) 2
#define DT_PROP(node, property) TEST_PROPERTY_##property
#define DT_PROP_OR(node, property, fallback) TEST_PROPERTY_##property
#define TEST_PROPERTY_full_ohms 20000
#define TEST_PROPERTY_output_ohms 10000
#define TEST_PROPERTY_power_on_sample_delay_us 2000
#define DT_NODE_HAS_PROP(node, property) TEST_BATTERY_SWITCHED
#define ADC_DT_SPEC_GET(node) { &test_adc, { false }, 12 }
bool adc_is_ready_dt(const adc_dt_spec * spec);
int adc_channel_setup_dt(const adc_dt_spec * spec);
int adc_sequence_init_dt(const adc_dt_spec * spec, adc_sequence * sequence);
int adc_read_dt(const adc_dt_spec * spec, const adc_sequence * sequence);
int adc_raw_to_millivolts_dt(const adc_dt_spec * spec, int32_t * value);
