diff --git chrome/browser/extensions/api/runtime/chrome_runtime_api_delegate.cc chrome/browser/extensions/api/runtime/chrome_runtime_api_delegate.cc
index b39405513b9e..d02005cfda0a 100644
--- chrome/browser/extensions/api/runtime/chrome_runtime_api_delegate.cc
+++ chrome/browser/extensions/api/runtime/chrome_runtime_api_delegate.cc
@@ -291,6 +291,8 @@ bool ChromeRuntimeAPIDelegate::GetPlatformInfo(PlatformInfo* info) {
     info->os = extensions::api::runtime::PlatformOs::kLinux;
   } else if (strcmp(os, "freebsd") == 0) {
     info->os = extensions::api::runtime::PlatformOs::kLinux;
+  } else if (strcmp(os, "dragonfly") == 0) {
+    info->os = extensions::api::runtime::PlatformOs::kLinux;
   } else {
     NOTREACHED() << "Platform not supported: " << os;
   }
