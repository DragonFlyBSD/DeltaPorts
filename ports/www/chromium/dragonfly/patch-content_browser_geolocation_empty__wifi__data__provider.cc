--- content/browser/geolocation/empty_wifi_data_provider.cc.intermediate	2014-01-05 13:16:12.142240000 +0000
+++ content/browser/geolocation/empty_wifi_data_provider.cc
@@ -19,7 +19,8 @@ bool EmptyWifiDataProvider::GetData(Wifi
 }
 
 // Only define for platforms that lack a real wifi data provider.
-#if !defined(OS_WIN) && !defined(OS_MACOSX) && !defined(OS_LINUX) && !defined(OS_FREEBSD)
+#if !defined(OS_WIN) && !defined(OS_MACOSX) && !defined(OS_LINUX) \
+ && !defined(OS_FREEBSD) && !defined(OS_DRAGONFLY)
 // static
 WifiDataProviderImplBase* WifiDataProvider::DefaultFactoryFunction() {
   return new EmptyWifiDataProvider();
