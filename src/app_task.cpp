// SPDX-License-Identifier: GPL-3.0-or-later
#include "app_task.h"
#include "board_power.h"
#include "boot_diagnostics.h"
#include "app/matter_init.h"
#include <app-common/zap-generated/attributes/Accessors.h>
#include <app/clusters/power-source-server/power-source-server.h>
#include <app/clusters/identify-server/identify-server.h>
#include <app/icd/server/ICDNotifier.h>
#include <app/server/Server.h>
#include <lib/support/CodeUtils.h>
#include <platform/CHIPDeviceLayer.h>
#include <setup_payload/OnboardingCodesUtil.h>
#include <zephyr/irq.h>
#include <zephyr/logging/log.h>
#ifdef CONFIG_SHELL
#include <zephyr/shell/shell.h>
#endif

LOG_MODULE_DECLARE(app, CONFIG_CHIP_APP_LOG_LEVEL);
using namespace chip;
using namespace chip::app;
using namespace chip::app::Clusters;
using namespace chip::DeviceLayer;
namespace {
constexpr EndpointId kMotionEndpoint = 1;
constexpr EndpointId kBatteryEndpoint = 2;
// Register Identify lifecycle even when the low-power profile has no visible indicator.
::Identify sIdentify{kMotionEndpoint, [](::Identify *) {}, [](::Identify *) {},
                     Clusters::Identify::IdentifyTypeEnum::kNone};
constexpr uint32_t kMotionHoldMs = 5000;
constexpr uint32_t kSensorRetryMs = 60000;
constexpr uint32_t kBatteryIntervalMs = 24 * 60 * 60 * 1000;
constexpr gpio_dt_spec kMotionPin = GPIO_DT_SPEC_GET(DT_ALIAS(mailbox_motion), gpios);
constexpr gpio_dt_spec kFactoryResetPin = GPIO_DT_SPEC_GET(DT_ALIAS(mailbox_factory_reset), gpios);
K_SEM_DEFINE(sWake, 0, 1);

#ifdef CONFIG_SHELL
int MailboxCommand(const shell *sh, size_t argc, char **argv)
{
    if (argc != 2) { shell_error(sh, "Use mailbox status|motion|battery|commission"); return -EINVAL; }
    AppTask::Event event;
    if (!strcmp(argv[1], "status")) event = AppTask::Status;
    else if (!strcmp(argv[1], "motion")) event = AppTask::TestMotion;
    else if (!strcmp(argv[1], "battery")) event = AppTask::Battery;
    else if (!strcmp(argv[1], "commission")) event = AppTask::Commission;
    else return -EINVAL;
    AppTask::Instance().PostEvent(event);
    return 0;
}
SHELL_CMD_ARG_REGISTER(mailbox, NULL, "Mailbox diagnostics: status, motion, battery, commission", MailboxCommand, 2, 0);
#endif
}

AppTask &AppTask::Instance() { static AppTask app; return app; }
void AppTask::PostEvent(Event event)
{
    // Persistent bits survive concurrent IRQ/timer activity and a full wake semaphore.
    atomic_or(&mEvents, event);
    k_sem_give(&sWake);
}
void AppTask::MotionInterrupt(const device *, gpio_callback *, gpio_port_pins_t)
{
    gpio_pin_interrupt_configure_dt(&kMotionPin, GPIO_INT_DISABLE);
    Instance().PostEvent(Motion);
}
void AppTask::MotionTimeout(k_timer *) { Instance().PostEvent(MotionExpired); }
void AppTask::SensorTimeout(k_timer *) { Instance().PostEvent(RetrySensor); }
void AppTask::BatteryTimeout(k_timer *) { Instance().PostEvent(Battery); }
void AppTask::FactoryResetInterrupt(const device *, gpio_callback *, gpio_port_pins_t)
{
    auto &app = Instance();
    atomic_inc(&app.mFactoryResetEdges);
    app.PostEvent(ResetButton);
}
void AppTask::FactoryResetTimeout(k_timer *) { Instance().PostEvent(ResetButtonTimer); }

CHIP_ERROR AppTask::Init()
{
    LOG_INF("Initializing mailbox hardware");
    // Early initialization sets board outputs and RF power before radios start.
    MailboxBootCheckpoint(BootDiagnosticStage::BoardPowerBegin, "before InitBoardPower");
    VerifyOrReturnError(InitBoardPower() == 0, CHIP_ERROR_INCORRECT_STATE);
    MailboxBootCheckpoint(BootDiagnosticStage::BoardPowerComplete, "after InitBoardPower");
    MailboxBootCheckpoint(BootDiagnosticStage::MotionGpioBegin, "before motion GPIO configure");
    VerifyOrReturnError(gpio_is_ready_dt(&kMotionPin), CHIP_ERROR_INCORRECT_STATE);
    VerifyOrReturnError(gpio_pin_configure_dt(&kMotionPin, GPIO_INPUT | GPIO_PULL_DOWN) == 0,
                        CHIP_ERROR_INCORRECT_STATE);
    MailboxBootCheckpoint(BootDiagnosticStage::MotionGpioComplete, "after motion GPIO configure");
    MailboxBootCheckpoint(BootDiagnosticStage::MotionCallbackBegin, "before motion GPIO callback");
    gpio_init_callback(&mMotionCallback, MotionInterrupt, BIT(kMotionPin.pin));
    VerifyOrReturnError(gpio_add_callback(kMotionPin.port, &mMotionCallback) == 0, CHIP_ERROR_INCORRECT_STATE);
    MailboxBootCheckpoint(BootDiagnosticStage::MotionCallbackComplete, "after motion GPIO callback");
    MailboxBootCheckpoint(BootDiagnosticStage::TimersBegin, "before application timers init");
    k_timer_init(&mMotionTimer, MotionTimeout, nullptr);
    k_timer_init(&mSensorTimer, SensorTimeout, nullptr);
    k_timer_init(&mBatteryTimer, BatteryTimeout, nullptr);
    k_timer_init(&mFactoryResetTimer, FactoryResetTimeout, nullptr);
    MailboxBootCheckpoint(BootDiagnosticStage::TimersComplete, "after application timers init");
    VerifyOrReturnError(gpio_is_ready_dt(&kFactoryResetPin), CHIP_ERROR_INCORRECT_STATE);
    VerifyOrReturnError(gpio_pin_configure_dt(&kFactoryResetPin, GPIO_INPUT | GPIO_PULL_UP) == 0,
                        CHIP_ERROR_INCORRECT_STATE);
    const int resetPressed = gpio_pin_get_dt(&kFactoryResetPin);
    VerifyOrReturnError(resetPressed >= 0, CHIP_ERROR_INCORRECT_STATE);
    mFactoryResetButton.Initialize(resetPressed > 0);
    gpio_init_callback(&mFactoryResetCallback, FactoryResetInterrupt, BIT(kFactoryResetPin.pin));
    VerifyOrReturnError(gpio_add_callback(kFactoryResetPin.port, &mFactoryResetCallback) == 0,
                        CHIP_ERROR_INCORRECT_STATE);
    VerifyOrReturnError(gpio_pin_interrupt_configure_dt(&kFactoryResetPin, GPIO_INT_EDGE_BOTH) == 0,
                        CHIP_ERROR_INCORRECT_STATE);
    // Catch a change between the initial sample and enabling the PORT interrupt.
    const int resetPressedAfterEnable = gpio_pin_get_dt(&kFactoryResetPin);
    VerifyOrReturnError(resetPressedAfterEnable >= 0, CHIP_ERROR_INCORRECT_STATE);
    if (resetPressedAfterEnable != resetPressed) {
        atomic_inc(&mFactoryResetEdges);
        PostEvent(ResetButton);
    }
    MailboxBootCheckpoint(BootDiagnosticStage::BatteryBegin, "before battery init");
    mBattery.Init();
    MailboxBootCheckpoint(BootDiagnosticStage::BatteryComplete, "after battery init");
    LOG_INF("Preparing Matter server");
    MailboxBootCheckpoint(BootDiagnosticStage::PrepareServerBegin, "before PrepareServer");
    ReturnErrorOnFailure(Nrf::Matter::PrepareServer());
    MailboxBootCheckpoint(BootDiagnosticStage::PrepareServerComplete, "after PrepareServer");
    LOG_INF("Starting Matter server");
    MailboxBootCheckpoint(BootDiagnosticStage::StartServerBegin, "before StartServer");
    ReturnErrorOnFailure(Nrf::Matter::StartServer());
    MailboxBootCheckpoint(BootDiagnosticStage::StartServerComplete, "after StartServer");
    LOG_INF("Initializing Matter endpoints");
    {
        StackLock lock;
        ReturnErrorOnFailure(mOccupancy.Init());
        const OccupancySensing::Structs::HoldTimeLimitsStruct::Type limits{5, 5, 5};
        ReturnErrorOnFailure(OccupancySensing::SetHoldTimeLimits(kMotionEndpoint, limits));
        ReturnErrorOnFailure(OccupancySensing::SetHoldTime(kMotionEndpoint, 5));
        EndpointId endpoints[] = { kMotionEndpoint };
        ReturnErrorOnFailure(PowerSourceServer::Instance().SetEndpointList(kBatteryEndpoint, Span<EndpointId>(endpoints)));
    }
    LOG_INF("Matter endpoints ready");
    SetOccupancy(false);
    RearmSensor();
    SampleBattery();
    OpenCommissioningWindow();
    PrintStatus();
    return CHIP_NO_ERROR;
}

CHIP_ERROR AppTask::StartApp()
{
    ReturnErrorOnFailure(Init());
    for (;;) {
        k_sem_take(&sWake, K_FOREVER);
        const atomic_val_t events = atomic_set(&mEvents, 0);
        if (events & (ResetButton | ResetButtonTimer)) HandleFactoryResetButton();
        if (mFactoryResetStarted) continue;
        if (events & MotionExpired) { SetOccupancy(false); RearmSensor(); }
        if (events & RetrySensor) { if (!mOccupied) RearmSensor(); }
        if (events & Motion) HandleMotion(false);
        if (events & TestMotion) HandleMotion(true);
        if (events & Battery) SampleBattery();
        if (events & Commission) OpenCommissioningWindow();
        if (events & Status) PrintStatus();
    }
}

void AppTask::HandleFactoryResetButton()
{
    if (mFactoryResetStarted) return;
    // Observe edges, the pin and the deadline together. An old timer event must
    // never count time from a press that has since been released/repressed.
    const unsigned int key = irq_lock();
    const uint64_t nowMs = k_uptime_get();
    const atomic_val_t edges = atomic_get(&mFactoryResetEdges);
    if (edges != mFactoryResetObservedEdges) {
        mFactoryResetObservedEdges = edges;
        mFactoryResetButton.Edge(nowMs);
    }
    const int pressed = gpio_pin_get_dt(&kFactoryResetPin);
    bool reset = false;
    if (pressed < 0) mFactoryResetButton.Cancel();
    else reset = mFactoryResetButton.Timer(pressed > 0, nowMs);
    const uint32_t delayMs = mFactoryResetButton.NextDelayMs(nowMs);
    irq_unlock(key);

    k_timer_stop(&mFactoryResetTimer);
    if (pressed < 0) {
        LOG_ERR("Cannot read factory reset button: %d", pressed);
    } else if (reset) {
        mFactoryResetStarted = true;
        gpio_pin_interrupt_configure_dt(&kFactoryResetPin, GPIO_INT_DISABLE);
        LOG_INF("Factory reset: erasing Matter fabrics, operational settings and Thread network");
        // SDK reset clears operational storage and reboots. Factory data (or
        // compile-time onboarding credentials) is retained for the next pairing.
        StackLock lock;
        Server::GetInstance().ScheduleFactoryReset();
    } else if (delayMs != FactoryResetButton::kNoTimer) {
        k_timer_start(&mFactoryResetTimer, K_MSEC(delayMs), K_NO_WAIT);
    }
}

void AppTask::RearmSensor()
{
    gpio_pin_interrupt_configure_dt(&kMotionPin, GPIO_INT_DISABLE);
    mSensorReady = mSensor.Init() == BMA400_OK;
    uint16_t ignored = 0;
    if (mSensorReady) mSensorReady = mSensor.ReadInterruptStatus(ignored) == BMA400_OK;
    if (mSensorReady) {
        int rc = gpio_pin_configure_dt(&kMotionPin, GPIO_INPUT);
        if (rc == 0) rc = gpio_pin_interrupt_configure_dt(&kMotionPin, GPIO_INT_EDGE_TO_ACTIVE);
        if (rc == 0) {
            k_timer_stop(&mSensorTimer);
            LOG_INF("BMA400 ready, chip=0x%02x", static_cast<unsigned>(mSensor.ChipId()));
            // Handle a level asserted before the rising-edge interrupt was enabled.
            if (gpio_pin_get_dt(&kMotionPin) > 0) {
                gpio_pin_interrupt_configure_dt(&kMotionPin, GPIO_INT_DISABLE);
                PostEvent(Motion);
            }
            return;
        }
        mSensorReady = false;
        LOG_ERR("Cannot enable motion IRQ: %d", rc);
    }
    gpio_pin_configure_dt(&kMotionPin, GPIO_INPUT | GPIO_PULL_DOWN);
    LOG_WRN("BMA400 unavailable (error=%d), retry in 60 seconds", mSensor.LastError());
    k_timer_start(&mSensorTimer, K_MSEC(kSensorRetryMs), K_NO_WAIT);
}

void AppTask::HandleMotion(bool simulated)
{
    if (mOccupied) return;
    gpio_pin_interrupt_configure_dt(&kMotionPin, GPIO_INT_DISABLE);
    k_timer_stop(&mSensorTimer);
    if (!simulated) {
        uint16_t status = 0;
        if (!mSensorReady || mSensor.ReadInterruptStatus(status) != BMA400_OK) {
            mSensorReady = false;
            RearmSensor();
            return;
        }
        LOG_INF("BMA400 interrupt=0x%04x", static_cast<unsigned>(status));
    }
    ++mMotionCount;
    SetOccupancy(true);
    k_timer_start(&mMotionTimer, K_MSEC(kMotionHoldMs), K_NO_WAIT);
}

void AppTask::SetOccupancy(bool occupied)
{
    BitMask<OccupancySensing::OccupancyBitmap> state;
    if (occupied) state.Set(OccupancySensing::OccupancyBitmap::kOccupied);
    StackLock lock;
    ICDNotifier::GetInstance().NotifyNetworkActivityNotification();
    const auto result = OccupancySensing::Attributes::Occupancy::Set(kMotionEndpoint, state);
    mOccupied = occupied;
    LOG_INF("Mailbox occupancy=%u endpoint=1 status=0x%02x", static_cast<unsigned>(occupied),
            static_cast<unsigned>(result));
}

void AppTask::SampleBattery()
{
    DataModel::Nullable<uint32_t> voltage;
    DataModel::Nullable<uint8_t> percentage;
    bool present = false, low = false;
    if (IS_ENABLED(CONFIG_MAILBOX_BATTERY_INSTALLED)) {
        const auto sample = mBattery.Sample();
        k_timer_start(&mBatteryTimer, K_MSEC(kBatteryIntervalMs), K_NO_WAIT);
        if (!sample.valid) {
            LOG_WRN("Battery ADC failed; retaining last valid attributes");
            if (mBatteryValid) return;
        } else {
            mBatteryValid = true;
            present = true;
            low = sample.low;
            voltage.SetNonNull(sample.millivolts);
            percentage.SetNonNull(sample.halfPercent);
            LOG_INF("Mailbox battery=%u mV charge=%u/200 low=%u", static_cast<unsigned>(sample.millivolts),
                    static_cast<unsigned>(sample.halfPercent), static_cast<unsigned>(low));
        }
    }
    StackLock lock;
    PowerSource::Attributes::Status::Set(kBatteryEndpoint, present ?
        PowerSource::PowerSourceStatusEnum::kActive : PowerSource::PowerSourceStatusEnum::kUnavailable);
    PowerSource::Attributes::BatPresent::Set(kBatteryEndpoint, present);
    PowerSource::Attributes::BatVoltage::Set(kBatteryEndpoint, voltage);
    PowerSource::Attributes::BatPercentRemaining::Set(kBatteryEndpoint, percentage);
    PowerSource::Attributes::BatReplacementNeeded::Set(kBatteryEndpoint, low);
    PowerSource::Attributes::BatChargeLevel::Set(kBatteryEndpoint, low ?
        PowerSource::BatChargeLevelEnum::kWarning : PowerSource::BatChargeLevelEnum::kOk);
}

void AppTask::PrintStatus()
{
    StackLock lock;
    auto &server = Server::GetInstance();
    LOG_INF("Mailbox sensor=%s occupancy=%u motion_count=%u fabrics=%u INT1=%d battery_installed=%u window=%u ble_enabled=%u ble_advertising=%u ble_connections=%u",
        mSensorReady ? "ready" : "unavailable", static_cast<unsigned>(mOccupied), mMotionCount,
        static_cast<unsigned>(server.GetFabricTable().FabricCount()), gpio_pin_get_dt(&kMotionPin),
        IS_ENABLED(CONFIG_MAILBOX_BATTERY_INSTALLED),
        static_cast<unsigned>(server.GetCommissioningWindowManager().IsCommissioningWindowOpen()),
        static_cast<unsigned>(ConnectivityMgr().IsBLEAdvertisingEnabled()),
        static_cast<unsigned>(ConnectivityMgr().IsBLEAdvertising()),
        static_cast<unsigned>(ConnectivityMgr().NumBLEConnections()));
}

void AppTask::OpenCommissioningWindow()
{
    StackLock lock;
    auto &server = Server::GetInstance();
    if (server.GetFabricTable().FabricCount() != 0) {
        LOG_INF("Commissioning skipped: device already has a fabric");
        return;
    }
    if (ConnectivityMgr().NumBLEConnections() != 0) {
        LOG_INF("Commissioning window/session already active");
        return;
    }
    if (server.GetCommissioningWindowManager().IsCommissioningWindowOpen())
        server.GetCommissioningWindowManager().CloseCommissioningWindow();
    const auto err = server.GetCommissioningWindowManager().OpenBasicCommissioningWindow(
        System::Clock::Seconds16(900), CommissioningWindowAdvertisement::kAllSupported);
    if (err != CHIP_NO_ERROR) { LOG_ERR("Commissioning failed: %" CHIP_ERROR_FORMAT, err.Format()); return; }
    PrintOnboardingCodes(RendezvousInformationFlags(RendezvousInformationFlag::kBLE));
    LOG_INF("Commissioning window open for 15 minutes");
}
