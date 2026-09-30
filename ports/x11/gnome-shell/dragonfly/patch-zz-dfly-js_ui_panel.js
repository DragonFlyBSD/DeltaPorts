--- js/ui/panel.js.orig	2026-07-04 15:11:49 UTC
+++ js/ui/panel.js
@@ -300,6 +300,12 @@ class QuickSettings extends PanelMenu.Button {
     }
 
     async _setupIndicators() {
+        // On DragonFly there is no NetworkManager and no Bluetooth, so the
+        // two conditional awaits below are skipped and this method would run
+        // synchronously during panel construction, before start() has set up
+        // globals such as Main.brightnessManager. Yield once so those exist.
+        await null;
+
         if (Config.HAVE_NETWORKMANAGER) {
             /** @type {import('./status/network.js')} */
             const NetworkStatus = await import('./status/network.js');
