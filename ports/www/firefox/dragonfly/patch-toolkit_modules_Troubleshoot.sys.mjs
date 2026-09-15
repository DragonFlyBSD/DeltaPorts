--- toolkit/modules/Troubleshoot.sys.mjs.orig
+++ toolkit/modules/Troubleshoot.sys.mjs
@@ -181,6 +181,10 @@
       let snapshot = {};
       let numPending = Object.keys(dataProviders).length;
       function providerDone(providerName, providerData) {
+        // A provider can finish before its returned promise rejects.
+        if (Object.hasOwn(snapshot, providerName)) {
+          return;
+        }
         snapshot[providerName] = providerData;
         if (--numPending == 0) {
           // Ensure that done is always and truly called asynchronously.
@@ -189,7 +193,13 @@
       }
       for (let name in dataProviders) {
         try {
-          dataProviders[name](providerDone.bind(null, name));
+          let result = dataProviders[name](providerDone.bind(null, name));
+          // Async failures must not leave the entire snapshot pending.
+          Promise.resolve(result).catch(err => {
+            let msg = "Troubleshoot data provider failed: " + name + "\n" + err;
+            console.error(msg);
+            providerDone(name, msg);
+          });
         } catch (err) {
           let msg = "Troubleshoot data provider failed: " + name + "\n" + err;
           console.error(msg);
