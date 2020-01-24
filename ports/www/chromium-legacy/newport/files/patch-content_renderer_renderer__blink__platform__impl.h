--- content/renderer/renderer_blink_platform_impl.h.orig	2019-10-21 19:06:33 UTC
+++ content/renderer/renderer_blink_platform_impl.h
@@ -30,7 +30,7 @@
 #include "third_party/blink/public/mojom/cache_storage/cache_storage.mojom.h"
 #include "third_party/blink/public/mojom/loader/code_cache.mojom.h"
 
-#if defined(OS_LINUX)
+#if defined(OS_LINUX) || defined(OS_BSD)
 #include "components/services/font/public/cpp/font_loader.h"  // nogncheck
 #include "third_party/skia/include/core/SkRefCnt.h"           // nogncheck
 #endif
@@ -225,7 +225,7 @@ class CONTENT_EXPORT RendererBlinkPlatformImpl : publi
 
   scoped_refptr<base::SingleThreadTaskRunner> io_runner_;
 
-#if defined(OS_LINUX) || defined(OS_MACOSX)
+#if defined(OS_LINUX) || defined(OS_MACOSX) || defined(OS_BSD)
   std::unique_ptr<blink::WebSandboxSupport> sandbox_support_;
 #endif
 
@@ -252,7 +252,7 @@ class CONTENT_EXPORT RendererBlinkPlatformImpl : publi
   std::unique_ptr<blink::WebTransmissionEncodingInfoHandler>
       web_transmission_encoding_info_handler_;
 
-#if defined(OS_LINUX)
+#if defined(OS_LINUX) || defined(OS_BSD)
   sk_sp<font_service::FontLoader> font_loader_;
 #endif
 
