--- chrome/common/pref_names.cc.orig	2021-01-18 21:28:52 UTC
+++ chrome/common/pref_names.cc
@@ -1089,7 +1089,7 @@ const char kForceYouTubeRestrict[] = "settings.force_y
 // only using an account that belongs to one of the domains from this pref.
 const char kAllowedDomainsForApps[] = "settings.allowed_domains_for_apps";
 
-#if defined(OS_LINUX) && !defined(OS_CHROMEOS)
+#if (defined(OS_LINUX) || defined(OS_BSD)) && !defined(OS_CHROMEOS)
 // Linux specific preference on whether we should match the system theme.
 const char kUsesSystemTheme[] = "extensions.theme.use_system";
 #endif
@@ -1246,7 +1246,7 @@ const char kShowUpdatePromotionInfoBar[] =
     "browser.show_update_promotion_info_bar";
 #endif
 
-#if defined(OS_LINUX) && !defined(OS_CHROMEOS)
+#if (defined(OS_LINUX) || defined(OS_BSD)) && !defined(OS_CHROMEOS)
 // Boolean that is false if we should show window manager decorations.  If
 // true, we draw a custom chrome frame (thicker title bar and blue border).
 const char kUseCustomChromeFrame[] = "browser.custom_chrome_frame";
@@ -1687,7 +1687,7 @@ const char kDownloadDefaultDirectory[] = "download.def
 // upgrade a unsafe location to a safe location.
 const char kDownloadDirUpgraded[] = "download.directory_upgrade";
 
-#if defined(OS_WIN) || defined(OS_LINUX) || defined(OS_CHROMEOS) || \
+#if defined(OS_WIN) || defined(OS_LINUX) || defined(OS_CHROMEOS) || defined(OS_BSD) || \
     defined(OS_MAC)
 const char kOpenPdfDownloadInSystemReader[] =
     "download.open_pdf_in_system_reader";
@@ -2044,12 +2044,12 @@ const char kAmbientAuthenticationInPrivateModesEnabled
 // requests.
 const char kBasicAuthOverHttpEnabled[] = "auth.basic_over_http_enabled";
 
-#if defined(OS_LINUX) || defined(OS_MAC) || defined(OS_CHROMEOS)
+#if defined(OS_LINUX) || defined(OS_MAC) || defined(OS_CHROMEOS) || defined(OS_BSD)
 // Boolean that specifies whether OK-AS-DELEGATE flag from KDC is respected
 // along with kAuthNegotiateDelegateAllowlist.
 const char kAuthNegotiateDelegateByKdcPolicy[] =
     "auth.negotiate_delegate_by_kdc_policy";
-#endif  // defined(OS_LINUX) || defined(OS_MAC) || defined(OS_CHROMEOS)
+#endif  // defined(OS_LINUX) || defined(OS_MAC) || defined(OS_CHROMEOS) || defined(OS_BSD)
 
 #if defined(OS_POSIX)
 // Boolean that specifies whether NTLMv2 is enabled.
@@ -2955,7 +2955,7 @@ const char kAutoplayWhitelist[] = "media.autoplay_whit
 const char kBlockAutoplayEnabled[] = "media.block_autoplay";
 #endif  // !defined(OS_ANDROID)
 
-#if defined(OS_LINUX) && !defined(OS_CHROMEOS)
+#if (defined(OS_LINUX) && !defined(OS_CHROMEOS)) || defined(OS_BSD)
 // Boolean that indicates if native notifications are allowed to be used in
 // place of Chrome notifications.
 const char kAllowNativeNotifications[] = "native_notifications.allowed";
