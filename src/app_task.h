// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "bma400/Bma400Motion.h"
#include "battery_monitor.h"
#include "factory_reset_button.h"
#include <app/clusters/occupancy-sensor-server/occupancy-sensor-server.h>
#include <lib/core/CHIPError.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/atomic.h>

class AppTask {
public:
    enum Event : atomic_val_t {
        Motion = BIT(0), MotionExpired = BIT(1), RetrySensor = BIT(2),
        Battery = BIT(3), Status = BIT(4), Commission = BIT(5), TestMotion = BIT(6),
        ResetButton = BIT(7), ResetButtonTimer = BIT(8)
    };
    static AppTask &Instance();
    CHIP_ERROR StartApp();
    void PostEvent(Event event);
private:
    CHIP_ERROR Init();
    void RearmSensor();
    void HandleMotion(bool simulated);
    void SetOccupancy(bool occupied);
    void SampleBattery();
    void PrintStatus();
    void OpenCommissioningWindow();
    void HandleFactoryResetButton();
    static void MotionInterrupt(const device *, gpio_callback *, gpio_port_pins_t);
    static void FactoryResetInterrupt(const device *, gpio_callback *, gpio_port_pins_t);
    static void MotionTimeout(k_timer *);
    static void SensorTimeout(k_timer *);
    static void BatteryTimeout(k_timer *);
    static void FactoryResetTimeout(k_timer *);
    Bma400Motion mSensor;
    BatteryMonitor mBattery;
    chip::app::Clusters::OccupancySensing::Instance mOccupancy{
        chip::BitMask<chip::app::Clusters::OccupancySensing::Feature>(
            chip::app::Clusters::OccupancySensing::Feature::kPassiveInfrared)};
    gpio_callback mMotionCallback{};
    gpio_callback mFactoryResetCallback{};
    k_timer mMotionTimer{}, mSensorTimer{}, mBatteryTimer{};
    k_timer mFactoryResetTimer{};
    FactoryResetButton mFactoryResetButton;
    atomic_t mFactoryResetEdges = ATOMIC_INIT(0);
    atomic_val_t mFactoryResetObservedEdges = 0;
    atomic_t mEvents = ATOMIC_INIT(0);
    bool mFactoryResetStarted = false;
    bool mSensorReady = false;
    bool mOccupied = false;
    bool mBatteryValid = false;
    uint32_t mMotionCount = 0;
};
