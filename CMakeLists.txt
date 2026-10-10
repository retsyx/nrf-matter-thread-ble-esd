# SPDX-License-Identifier: GPL-3.0-or-later
cmake_minimum_required(VERSION 3.20.0)
list(APPEND DTS_ROOT ${CMAKE_CURRENT_SOURCE_DIR})
# NCS 3.4.1 caches these internal header names without Kconfig string quotes.
# Do not let an incremental configure import them as command-line overrides.
foreach(header MBEDTLS_CONFIG_FILE TF_PSA_CRYPTO_CONFIG_FILE TF_PSA_CRYPTO_USER_CONFIG_FILE)
    unset(CONFIG_${header} CACHE)
    unset(CLI_CONFIG_${header} CACHE)
endforeach()
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(nrf_mailbox_motion)
set(MAILBOX_ZAP_EXECUTABLE "$ENV{ZAP_CLI}" CACHE FILEPATH "Installed ZAP CLI executable")
if(NOT MAILBOX_ZAP_EXECUTABLE)
    find_program(MAILBOX_ZAP_EXECUTABLE_PATH NAMES zap-cli REQUIRED)
    set(MAILBOX_ZAP_EXECUTABLE "${MAILBOX_ZAP_EXECUTABLE_PATH}")
endif()
set(MAILBOX_MATTER_OUTPUT "${CMAKE_BINARY_DIR}/matter")
# Matter's Python codegen reads the IDL during configuration, so generate both
# the IDL and ZAP sources here, before configuring the SDK data model.
file(GLOB_RECURSE MAILBOX_ZAP_TEMPLATES CONFIGURE_DEPENDS
    "${ZEPHYR_CONNECTEDHOMEIP_MODULE_DIR}/src/app/zap-templates/*"
    "${ZEPHYR_CONNECTEDHOMEIP_MODULE_DIR}/src/app/common/templates/*"
)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/src/data_model/mailbox-motion.zap"
    "${CMAKE_CURRENT_SOURCE_DIR}/scripts/generate.py"
    "${CMAKE_CURRENT_SOURCE_DIR}/scripts/nordic.py"
    "${MAILBOX_ZAP_EXECUTABLE}"
    ${MAILBOX_ZAP_TEMPLATES}
)
execute_process(
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/scripts/generate.py"
        --zap "${MAILBOX_ZAP_EXECUTABLE}" --output-dir "${MAILBOX_MATTER_OUTPUT}"
    RESULT_VARIABLE MAILBOX_ZAP_RESULT
)
if(NOT MAILBOX_ZAP_RESULT EQUAL 0)
    message(FATAL_ERROR "Matter generation failed. Select an installed compatible ZAP CLI with --zap or ZAP_CLI.")
endif()
# Reconfigure if a generated file is removed, including builds run directly
# through Ninja rather than the Python wrapper.
file(GLOB_RECURSE MAILBOX_MATTER_GENERATED_FILES "${MAILBOX_MATTER_OUTPUT}/*")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS ${MAILBOX_MATTER_GENERATED_FILES})
include(${ZEPHYR_CONNECTEDHOMEIP_MODULE_DIR}/config/nrfconnect/app/enable-gnu-std.cmake)
set(MATTER_COMMON ${ZEPHYR_NRF_MODULE_DIR}/samples/matter/common/src)
target_include_directories(app PRIVATE src ${MAILBOX_MATTER_OUTPUT} ${MATTER_COMMON})
target_sources(app PRIVATE
    src/main.cpp src/app_task.cpp src/battery_monitor.cpp src/board_power.cpp src/radio_power.cpp
    src/bma400/Bma400Motion.cpp src/bma400/bma400.c
    ${MATTER_COMMON}/app/matter_init.cpp
    ${MATTER_COMMON}/app/matter_event_handler.cpp
    ${MATTER_COMMON}/clusters/cluster_init.cpp
)
zephyr_linker_sources(SECTIONS ${MATTER_COMMON}/clusters/cluster_init.ld)
target_sources_ifdef(CONFIG_MAILBOX_BOOT_DIAGNOSTICS app PRIVATE src/boot_diagnostics.cpp)
target_sources_ifdef(CONFIG_MAILBOX_BOOT_DIAGNOSTICS app PRIVATE src/boot_crash_diagnostics.cpp)
zephyr_link_libraries_ifdef(CONFIG_MAILBOX_BOOT_DIAGNOSTICS -Wl,--wrap=abort)
target_include_directories(matter-data-model PUBLIC "${MAILBOX_MATTER_OUTPUT}")
include(${ZEPHYR_CONNECTEDHOMEIP_MODULE_DIR}/src/app/chip_data_model.cmake)
chip_configure_data_model(matter-data-model
    BYPASS_IDL
    ZAP_FILE ${CMAKE_CURRENT_SOURCE_DIR}/src/data_model/mailbox-motion.zap
    IDL "${MAILBOX_MATTER_OUTPUT}/mailbox-motion.matter"
    GEN_DIR "${MAILBOX_MATTER_OUTPUT}/zap-generated"
)
