--- js/misc/loginManager.js.orig
+++ js/misc/loginManager.js
@@ -47,7 +47,12 @@
             -1, null);
 
         const version = result.deepUnpack()[0].deepUnpack();
-        return haveSystemd() && versionCompare('3.5.91', version);
+        // DragonFly has no logind, but seatd (its logind replacement for seat
+        // management) plus GDM provide the greeter/lock UI. haveSystemd() must
+        // stay false so getLoginManager() keeps using the Dummy backend, so
+        // gate the lock capability on seatd directly here.
+        const haveSeatManagement = haveSystemd() || GLib.access('/var/run/seatd.sock', 0) >= 0;
+        return haveSeatManagement && versionCompare('3.5.91', version);
     } catch {
         return false;
     }
@@ -230,6 +235,27 @@
     }
 }
 
+// DragonFly has no logind session-bus object. This stand-in gives screenShield
+// a session proxy whose connectSignal('Unlock', ...) actually connects, plus an
+// emitUnlock() that reauthentication calls on success so the shield leaves the
+// lock screen. logind's real proxy delivers 'Unlock' as a D-Bus signal; here it
+// is synthesized locally.
+class DummySessionProxy extends Signals.EventEmitter {
+    connectSignal(signalName, callback) {
+        // Mirror GDBusProxy.connectSignal: invoke the callback as
+        // (proxy, senderName, params). screenShield's handlers ignore them.
+        return this.connect(signalName, () => callback(this, null, []));
+    }
+
+    disconnectSignal(id) {
+        this.disconnect(id);
+    }
+
+    emitUnlock() {
+        this.emit('Unlock');
+    }
+}
+
 class LoginManagerDummy extends Signals.EventEmitter  {
     constructor() {
         super();
@@ -244,10 +270,15 @@
     }
 
     getCurrentSessionProxy() {
-        // we could return a DummySession object that fakes whatever callers
-        // expect (at the time of writing: connect() and connectSignal()
-        // methods), but just never settling the promise should be safer
-        return new Promise(() => {});
+        // DragonFly: no logind. Return a minimal session proxy that supports
+        // connectSignal() so screenShield can wire up its 'Lock'/'Unlock'
+        // handlers, and that reauthentication fires 'Unlock' on when screen
+        // unlock succeeds (see ShellUserVerifier._onVerificationComplete).
+        // The upstream never-settling promise leaves screenShield permanently
+        // unable to unlock because that await never returns.
+        if (!this._sessionProxy)
+            this._sessionProxy = new DummySessionProxy();
+        return Promise.resolve(this._sessionProxy);
     }
 
     canRebootToBootLoaderMenu() {
