--- common/alformat.hpp.orig	2026-01-20 02:56:03 UTC
+++ common/alformat.hpp
@@ -6,9 +6,10 @@
 #endif
 
 /* On macOS, std::format requires std::to_chars, which isn't available prior
- * to macOS 13.3.
+ * to macOS 13.3. Likewise, DragonFly's default GCC (12) libstdc++ does not
+ * provide <format> yet, so use the bundled fmt there as well.
  */
-#if defined(MAC_OS_X_VERSION_MIN_REQUIRED) && MAC_OS_X_VERSION_MIN_REQUIRED < 130300
+#if defined(__DragonFly__) || (defined(MAC_OS_X_VERSION_MIN_REQUIRED) && MAC_OS_X_VERSION_MIN_REQUIRED < 130300)
 #include "fmt/format.h"
 
 namespace al {
