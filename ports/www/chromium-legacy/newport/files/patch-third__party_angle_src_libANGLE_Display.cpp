--- third_party/angle/src/libANGLE/Display.cpp.orig	2019-10-21 19:09:09 UTC
+++ third_party/angle/src/libANGLE/Display.cpp
@@ -68,7 +68,7 @@
 #if defined(ANGLE_ENABLE_VULKAN)
 #    if defined(ANGLE_PLATFORM_WINDOWS)
 #        include "libANGLE/renderer/vulkan/win32/DisplayVkWin32.h"
-#    elif defined(ANGLE_PLATFORM_LINUX)
+#    elif defined(ANGLE_PLATFORM_POSIX)
 #        include "libANGLE/renderer/vulkan/xcb/DisplayVkXcb.h"
 #    elif defined(ANGLE_PLATFORM_ANDROID)
 #        include "libANGLE/renderer/vulkan/android/DisplayVkAndroid.h"
@@ -268,7 +268,7 @@ rx::DisplayImpl *CreateDisplayFromAttribs(const Attrib
 #if defined(ANGLE_ENABLE_VULKAN)
 #    if defined(ANGLE_PLATFORM_WINDOWS)
             impl = new rx::DisplayVkWin32(state);
-#    elif defined(ANGLE_PLATFORM_LINUX)
+#    elif defined(ANGLE_PLATFORM_POSIX)
             impl = new rx::DisplayVkXcb(state);
 #    elif defined(ANGLE_PLATFORM_ANDROID)
             impl = new rx::DisplayVkAndroid(state);
