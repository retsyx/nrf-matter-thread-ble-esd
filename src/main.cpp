// SPDX-License-Identifier: GPL-3.0-or-later
#include "app_task.h"
#include "boot_crash_diagnostics.h"
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
LOG_MODULE_REGISTER(app, CONFIG_CHIP_APP_LOG_LEVEL);
int main()
{
#if defined(CONFIG_MAILBOX_BOOT_DIAGNOSTICS)
    LOG_INF("Boot stage: main entered");
    /* Let logging/shell threads and the host observe USB before app init. */
    k_sleep(K_SECONDS(5));
    MailboxBootCrashReportAndIdle();
#endif
    const CHIP_ERROR err = AppTask::Instance().StartApp();
    LOG_ERR("Application stopped: %" CHIP_ERROR_FORMAT, err.Format());
    return err == CHIP_NO_ERROR ? 0 : 1;
}
