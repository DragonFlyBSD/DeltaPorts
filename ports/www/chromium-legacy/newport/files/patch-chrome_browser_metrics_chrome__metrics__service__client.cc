--- chrome/browser/metrics/chrome_metrics_service_client.cc.orig	2021-01-18 21:28:50 UTC
+++ chrome/browser/metrics/chrome_metrics_service_client.cc
@@ -696,11 +696,11 @@ void ChromeMetricsServiceClient::RegisterMetricsServic
 #endif  // defined(OS_WIN)
 
 #if defined(OS_WIN) || defined(OS_MAC) || \
-    (defined(OS_LINUX) && !defined(OS_CHROMEOS))
+    (defined(OS_LINUX) && !defined(OS_CHROMEOS)) || defined(OS_BSD)
   metrics_service_->RegisterMetricsProvider(
       std::make_unique<DesktopPlatformFeaturesMetricsProvider>());
 #endif  //  defined(OS_WIN) || defined(OS_MAC) || \
-        // (defined(OS_LINUX) && !defined(OS_CHROMEOS))
+        // (defined(OS_LINUX) && !defined(OS_CHROMEOS)) || defined(OS_BSD)
 
 #if BUILDFLAG(ENABLE_PLUGINS)
   plugin_metrics_provider_ = new PluginMetricsProvider(local_state);
@@ -974,7 +974,7 @@ bool ChromeMetricsServiceClient::RegisterForProfileEve
   }
 #endif
 #if defined(OS_WIN) || defined(OS_MAC) || \
-    (defined(OS_LINUX) && !defined(OS_CHROMEOS))
+    (defined(OS_LINUX) && !defined(OS_CHROMEOS)) || defined(OS_BSD)
   // This creates the DesktopProfileSessionDurationsServices if it didn't exist
   // already.
   metrics::DesktopProfileSessionDurationsServiceFactory::GetForBrowserContext(
