--- src/main.ts.orig
+++ src/main.ts
@@ -146,7 +146,7 @@
 // Pseudo Language Language Pack is being used.
 // In that case, use `en` as the Electron locale.
 
-if (process.platform === 'win32' || process.platform === 'linux' || process.platform === 'freebsd') {
+if (process.platform === 'win32' || process.platform === 'linux' || process.platform === 'freebsd' || process.platform === 'dragonfly') {
 	const electronLocale = (!userLocale || userLocale === 'qps-ploc') ? 'en' : userLocale;
 	app.commandLine.appendSwitch('lang', electronLocale);
 }
@@ -238,7 +238,7 @@
 		'remote-debugging-port'
 	];
 
-	if (process.platform === 'linux' || process.platform === 'freebsd') {
+	if (process.platform === 'linux' || process.platform === 'freebsd' || process.platform === 'dragonfly') {
 
 		// Force enable screen readers on Linux via this flag
 		SUPPORTED_ELECTRON_SWITCHES.push('force-renderer-accessibility');
@@ -334,7 +334,7 @@
 	// `EstablishGpuChannelAsync` - Refs https://issues.chromium.org/issues/40208065
 	// `GlobalShortcutsPortal` - Enables Electron's `globalShortcut` (system-wide keybindings) on Linux Wayland via the XDG global shortcuts portal (no-op elsewhere)
 	const featuresToEnable =
-		`NetAdapterMaxBufSizeFeature:NetAdapterMaxBufSize/8192,DocumentPolicyIncludeJSCallStacksInCrashReports,EarlyEstablishGpuChannel,EstablishGpuChannelAsync${(process.platform === 'linux' || process.platform === 'freebsd') ? ',GlobalShortcutsPortal' : ''},${app.commandLine.getSwitchValue('enable-features')}`;
+		`NetAdapterMaxBufSizeFeature:NetAdapterMaxBufSize/8192,DocumentPolicyIncludeJSCallStacksInCrashReports,EarlyEstablishGpuChannel,EstablishGpuChannelAsync${(process.platform === 'linux' || process.platform === 'freebsd' || process.platform === 'dragonfly') ? ',GlobalShortcutsPortal' : ''},${app.commandLine.getSwitchValue('enable-features')}`;
 	app.commandLine.appendSwitch('enable-features', featuresToEnable);
 
 	// Following features are disabled from the runtime:
