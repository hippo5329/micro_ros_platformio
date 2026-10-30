// Wraps the ESP-IDF sdkconfig.h for the micro-ROS library build only.
//
// ESP32 Arduino core 3.x sets CONFIG_COMPILER_ASSERT_NDEBUG_EVALUATE, which
// makes ESP-IDF's assert() still evaluate its argument under NDEBUG. micro-ROS
// is built as Release (NDEBUG), and rosidl's generated code only declares the
// objects its asserts reference when NDEBUG is not defined, so evaluating
// them fails to compile. Restore the standard NDEBUG behaviour.
#pragma once
#include_next <sdkconfig.h>
#undef CONFIG_COMPILER_ASSERT_NDEBUG_EVALUATE
