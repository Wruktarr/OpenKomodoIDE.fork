/**
 * MobWrite - Real-time Synchronization and Collaboration Service
 *
 * Copyright 2006 Google Inc.
 * http://code.google.com/p/google-mobwrite/
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

/**
 * @fileoverview This client-side code drives the synchronisation.
 * @author fraser@google.com (Neil Fraser)
 */


/**
 * Singleton class containing all MobWrite code.
 */
var mobwrite = {};


/**
 * URL of Ajax gateway.
 * @type {string}
 */
mobwrite.__defineGetter__("syncGateway", function() {
  delete mobwrite.syncGateway;
  return mobwrite.syncGateway =
    ko.collaboration.service.prefs.getCharPref("syncURL");
});


/**
 * Max size of remote JSON-P gets.
 * @type {number}
 */
mobwrite.get_maxchars = 1000;


/**
 * Print diagnostic messages to the browser's console.
 * @type {boolean}
 */
mobwrite.__defineGetter__("debug", function() {
  delete mobwrite.debug;
  return mobwrite.debug =
    ko.collaboration.service.prefs.getBoolPref("mobwriteDebug");
});


/**
 * True if a connection error occured during a sync run. Set to false
 * automatically by the next sync run that succeeds.
 */
mobwrite.connectionError = false;

mobwrite.log = ko.logging.getLogger("mobwrite");

if (mobwrite.debug) {
  mobwrite.log.setLevel(ko.logging.LOG_DEBUG);
} else {
  mobwrite.log.setLevel(ko.logging.LOG_WARN);
}


/**
 * Browser sniff. Required to work around bugs in common implementations.
 * Sets mobwrite's UA_* properties.
 */
mobwrite.sniffUserAgent = function() {
  if (window.opera) {
    mobwrite.UA_opera = true;
  } else {
    var UA = navigator.userAgent.toLowerCase();
    mobwrite.UA_webkit = UA.indexOf('webkit') != -1;
    // Safari claims to be 'like Gecko'
    if (!mobwrite.UA_webkit) {
      mobwrite.UA_gecko = UA.indexOf('gecko') != -1;
      if (!mobwrite.UA_gecko) {
        // Test last, everyone wants to be like IE.
        mobwrite.UA_msie = UA.indexOf('msie') != -1;
      }
    }
  }
};

mobwrite.UA_gecko = false;
mobwrite.UA_opera = false;
mobwrite.UA_msie = false;
mobwrite.UA_webkit = false;
mobwrite.sniffUserAgent();


/**
 * PID of task which will trigger next Ajax request.
 * @type {number?}
 * @private
 */
mobwrite.syncRunPid_ = null;


/**
 * PID of task which will kill stalled Ajax request.
 * @type {number?}
 * @private
 */
mobwrite.syncKillPid_ = null;


/**
 * Time to wait for a connection before giving up and retrying.
 * @type {number}
 */
mobwrite.timeoutInterval = 10000;


/**
 * Delay in miliseconds for "immediate" syncs (e.g. after the client text has
 * been modified locally).
 * @type {number}
 */
mobwrite.minSyncInterval = 150;


/**
 * Longest interval (in milliseconds) between connections. Currently unused.
 * Look at mobwrite.pollingRun
 * @type {number}
 */
mobwrite.maxSyncInterval = 3000;


/**
 * Initial interval (in milliseconds) for connections.
 * This value is modified later as traffic rates are established.
 * Currently unused. Look at mobwrite.pollingRun
 * @type {number}
 */
mobwrite.syncInterval = 200;


/**
 * Usually sync is performed when a push notification arrives at
 * Collaboration.socket. Additionally we poll the server every 5 seconds (if
 * no other sync is running) in order to keep idling clients from being cleaned
 * up on the server.
 * @type number
 */
mobwrite.pollingInterval = 5000;


/**
 * Optional prefix to automatically add to all IDs.
 * @type {string}
 */
mobwrite.idPrefix = '';


/**
 * Flag to nullify all shared elements and terminate.
 * @type {boolean}
 */
mobwrite.nullifyAll = false;


/**
 * Array of ids to be nullified in the next sync run.
 */
mobwrite.nullifyIds = [];


/**
 * Track whether something changed client-side in each sync.
 * @type {boolean}
 * @private
 */
mobwrite.clientChange_ = false;


/**
 * Track whether something changed server-side in each sync.
 * @type {boolean}
 * @private
 */
mobwrite.serverChange_ = false;


/**
 * Temporary object used while each sync is airborne.
 * @type {Object?}
 * @private
 */
mobwrite.syncAjaxObj_ = null;


/**
 * Return a random id that's 8 letters long.
 * 26*(26+10+4)^7 = 4,259,840,000,000
 * @return {string} Random id.
 */
mobwrite.uniqueId = function() {
  // First character must be a letter.
  // IE is case insensitive (in violation of the W3 spec).
  var soup = 'abcdefghijklmnopqrstuvwxyz';
  var id = soup.charAt(Math.random() * soup.length);
  // Subsequent characters may include these.
  soup += '0123456789-_:.';
  for (var x = 1; x < 8; x++) {
    id += soup.charAt(Math.random() * soup.length);
  }
  // Don't allow IDs with '--' in them since it might close a comment.
  if (id.indexOf('--') != -1) {
    id = mobwrite.uniqueId();
  }
  return id;
  // Getting the maximum possible density in the ID is worth the extra code,
  // since the ID is transmitted to the server a lot.
};


/**
 * Unique ID for this session.
 * @type {string}
 */
mobwrite.syncUsername = mobwrite.uniqueId();


/**
 * Hash of all shared objects.
 * @type {Object}
 */
mobwrite.shared = {};


/**
 * Array of registered handlers for sharing types.
 * Modules add their share functions to this list.
 * @type {Array.<Function>}
 */
mobwrite.shareHandlers = [];


/**
 * Prototype of shared object.
 * @param {string} id Unique file ID.
 * @constructor
 */
mobwrite.shareObj = function(id) {
  if (id) {
    this.file = id;
    this.dmp = new diff_match_patch();
    this.dmp.Diff_Timeout = 0.5;
    // List of unacknowledged edits sent to the server.
    this.editStack = [];
    if (mobwrite.debug) {
      mobwrite.log.info('Creating shareObj: "' + id + '"');
    }
  }
};


/**
 * Client's understanding of what the server's text looks like.
 * @type {string}
 */
mobwrite.shareObj.prototype.shadowText = '';


/**
 * The client's version for the shadow (n).
 * @type {number}
 */
mobwrite.shareObj.prototype.clientVersion = 0;


/**
 * The server's version for the shadow (m).
 * @type {number}
 */
mobwrite.shareObj.prototype.serverVersion = 0;


/**
 * Did the client understand the server's delta in the previous heartbeat?
 * Initialize false because the server and client are out of sync initially.
 * @type {boolean}
 */
mobwrite.shareObj.prototype.deltaOk = false;


/**
 * Synchronization mode.
 * True: Used for text, attempts to gently merge differences together.
 * False: Used for numbers, overwrites conflicts, last save wins.
 * @type {boolean}
 */
mobwrite.shareObj.prototype.mergeChanges = true;


/**
 * Fetch or compute a plaintext representation of the user's text.
 * @return {string} Plaintext content.
 */
mobwrite.shareObj.prototype.getClientText = function() {
  window.alert('Defined by subclass');
  return '';
};


/**
 * Set the user's text based on the provided plaintext.
 * @param {string} text New text.
 */
mobwrite.shareObj.prototype.setClientText = function(text) {
  window.alert('Defined by subclass');
};


/**
 * Modify the user's plaintext by applying a series of patches against it.
 * @param {Array.<patch_obj>} patches Array of Patch objects.
 */
mobwrite.shareObj.prototype.patchClientText = function(patches) {
  var oldClientText = this.getClientText();
  var result = this.dmp.patch_apply(patches, oldClientText);
  // Set the new text only if there is a change to be made.
  if (oldClientText != result[0]) {
    // The following will probably destroy any cursor or selection.
    // Widgets with cursors should override and patch more delicately.
    this.setClientText(result[0]);
  }
};


/**
 * Notification of when a diff was sent to the server.
 * @param {Array.<Array.<*>>} diffs Array of diff tuples.
 */
mobwrite.shareObj.prototype.onSentDiff = function(diffs) {
  // Potential hook for subclass.
};


/**
 * Fire a synthetic 'change' event to a target element.
 * Notifies an element that its contents have been changed.
 * @param {Object} target Element to notify.
 */
mobwrite.shareObj.prototype.fireChange = function(target) {
  if ('createEvent' in document) {  // W3
    var e = document.createEvent('HTMLEvents');
    e.initEvent('change', false, false);
    target.dispatchEvent(e);
  } else if ('fireEvent' in target) { // IE
    target.fireEvent('onchange');
  }
};


/**
 * Return the command to nullify this file.  Also unshares this file.
 * @return {string} Command to be sent to the server.
 */
mobwrite.shareObj.prototype.nullify = function() {
  mobwrite.unshare(this.file);
  return 'N:' + mobwrite.idPrefix + this.file + '\n';
};


/**
 * Mark this file to be nullified. In the next syncRun this file will be
 * unshared and nullified on the server.
 */
mobwrite.shareObj.prototype.markNullified = function() {
  this._nullified = true;
}


/**
 * Returns true if the server should be told to nullify this file.
 */
mobwrite.shareObj.prototype.shouldBeNullified = function() {
  if (mobwrite.nullifyAll || this._nullified) {
    return true;
  }
  return false;
}

/**
 * Return the command to close this view of the fiele.  Also unshares the file.
 * @return {string} Command to be sent to the server.
 */
mobwrite.shareObj.prototype.close = function() {
  mobwrite.unshare(this.file);
  return 'x:' + mobwrite.idPrefix + this.file + '\n';
};

/**
 * Mark this file to be closed. In the next syncRun this file will be unshared
 * and the server will delete the corresponding view.
 */
mobwrite.shareObj.prototype.markClosed = function() {
  this._closed = true;
}


/**
 * Returns true if mobwrite should unshare this file and close the
 * corresponding view on the server.
 */
mobwrite.shareObj.prototype.shouldBeClosed = function() {
  return !!this._closed;
}


/**
 * Asks the shareObj to synchronize.  Computes client-made changes since
 * previous postback.  Return '' to skip this synchronization.
 * @return {string} Commands to be sent to the server.
 */
mobwrite.shareObj.prototype.syncText = function() {
  var clientText = this.getClientText();
  if (this.deltaOk) {
    // The last delta postback from the server to this shareObj was successful.
    // Send a compressed delta.
    var diffs = this.dmp.diff_main(this.shadowText, clientText, true);
    if (diffs.length > 2) {
      this.dmp.diff_cleanupSemantic(diffs);
      this.dmp.diff_cleanupEfficiency(diffs);
    }
    var changed = diffs.length != 1 || diffs[0][0] != DIFF_EQUAL;
    if (changed) {
// FIXME      mobwrite.clientChange_ = true;
      this.shadowText = clientText;
    }
    // Don't bother appending a no-change diff onto the stack if the stack
    // already contains something.
    if (changed || !this.editStack.length) {
      var action = (this.mergeChanges ? 'd:' : 'D:') + this.clientVersion +
          ':' + this.dmp.diff_toDelta(diffs);
      this.editStack.push([this.clientVersion, action]);
      this.clientVersion++;
      this.onSentDiff(diffs);
    }
  } else {
    // The last delta postback from the server to this shareObj didn't match.
    // Send a full text dump to get back in sync.  This will result in any
    // changes since the last postback being wiped out. :(
    this.shadowText = clientText;
    this.clientVersion++;
    var action = 'r:' + this.clientVersion + ':' +
                 encodeURI(clientText).replace(/%20/g, ' ');
    // Append the action to the edit stack.
    this.editStack.push([this.clientVersion, action]);
    // Sending a raw dump will put us back in sync.
    // Set deltaOk to true in case this sync fails to connect, in which case
    // the following sync(s) should be a delta, not more raw dumps.
    this.deltaOk = true;
  }

  // Create the output starting with the file statement, followed by the edits.
  var data = 'F:' + this.serverVersion + ':' +
      mobwrite.idPrefix + this.file + '\n';
  for (var x = 0; x < this.editStack.length; x++) {
    data += this.editStack[x][1] + '\n';
  }
  // Opera doesn't know how to encode char 0. (fixed in Opera 9.63)
  return data.replace(/\x00/g, '%00');
};


/**
 * Collect all client-side changes and send them to the server.
 * @private
 */
mobwrite.syncRun1_ = function() {
  // Check if we are logged in to SSO
  if (!mobwrite._checkLoginStatus()) {
    // No login data available. Stop sync task. pollingRun will automatically
    // try again.
    mobwrite.pauseSyncTask();
    return;
  }
  
  // Initialize clientChange_, to be checked at the end of syncRun2_.
  mobwrite.clientChange_ = false;
  var data = [];
  data[0] = 'u:' + mobwrite.syncUsername + '\n';
  var empty = true;
  // Ask every shared object for their deltas.
  for (var x in mobwrite.shared) {
    if (mobwrite.shared.hasOwnProperty(x)) {
      if (mobwrite.shared[x].shouldBeNullified()) {
        data.push(mobwrite.shared[x].nullify());
      } else if (mobwrite.shared[x].shouldBeClosed()) {
        data.push(mobwrite.shared[x].close());
      } else {
        var protocolMessage = mobwrite.shared[x].syncText();
        if (protocolMessage)
          data.push(protocolMessage);
      }
      empty = false;
    }
  }
  if (empty) {
    // No sync objects.
    if (mobwrite.debug) {
      mobwrite.log.info('MobWrite task stopped.');
    }
    if (mobwrite.pollingRunPid) {
      window.clearTimeout(mobwrite.pollingRunPid);
    }
    return;
  }
  if (data.length == 1) {
    // No sync data.
    if (mobwrite.debug) {
      mobwrite.log.info('All objects silent; null sync.');
    }
    mobwrite.syncRun2_('\n\n');
    return;
  }

  var remote = false;
//  var remote = (mobwrite.syncGateway.indexOf('://') != -1);
  if (mobwrite.debug) {
    mobwrite.log.info('TO server:\n' + data.join(''));
  }
  // Add terminating blank line.
  data.push('\n');
  data = data.join('');

  // Schedule a watchdog task to catch us if something horrible happens.
  mobwrite.syncKillPid_ =
      window.setTimeout(mobwrite.syncKill_, mobwrite.timeoutInterval);

  if (remote) {
    var blocks = mobwrite.splitBlocks_(data);
    // Add a script tag to the head.
    var head = document.getElementsByTagName('head')[0];
    for (var x = 0; x < blocks.length; x++) {
      var script = document.getElementById('mobwrite_sync' + x);
      if (script) {
        script.parentNode.removeChild(script);
        // IE allows us to recycle a script tag.
        // Other browsers need the old one destroyed and a new one created.
        if (!mobwrite.UA_msie) {
          // Browsers won't garbage collect the old script.
          // So castrate it to avoid a major memory leak.
          for (var prop in script) {
            delete script[prop];
          }
          script = null;
        }
      }
      if (!script) {
        script = document.createElement('script');
        script.type = 'text/javascript';
        script.charset = 'utf-8';
        script.id = 'mobwrite_sync' + x;
      }
      script.src = blocks[x];
      head.appendChild(script);
    }
    // Execution will resume in mobwrite.callback();
  } else {
    // Issue Ajax post of client-side changes and request server-side changes.
    data = 'q=' + encodeURIComponent(data);
    mobwrite.syncAjaxObj_ = mobwrite.syncLoadAjax_(mobwrite.syncGateway, data,
        mobwrite.syncCheckAjax_);
    // Execution will resume in either syncCheckAjax_(), or syncKill_()
  }
};


/**
 * Encode protocol data into JSONP URLs.  Split into multiple URLs if needed.
 * @param {string} data MobWrite protocol data.
 * @param {number} opt_minBlocks There will be at least this many blocks.
 * @return {Array.<string>} Protocol data split into smaller strings.
 * @private
 */
mobwrite.splitBlocks_ = function(data, opt_minBlocks) {
  var encData = encodeURIComponent(data);
  var prefix = mobwrite.syncGateway + '?p=';
  var maxchars = mobwrite.get_maxchars - prefix.length;
  var encPlusData = encData.replace(/%20/g, '+');
  if (encPlusData.length <= maxchars) {
    // Encode as single URL.
    return [prefix + encPlusData];
  }

  // Digits is the number of characters needed to encode the number of blocks.
  var digits = 1;
  if (typeof opt_minBlocks != 'undefined') {
    digits = String(opt_minBlocks).length;
  }

  // Break the data into small blocks.
  var blocks = [];
  // Encode the data again because it is being wrapped into another shell.
  var encEncData = encodeURIComponent(encData);
  // Compute the size of the overhead for each block.
  // Small bug: if there are 10+ blocks, we reserve but don't use one extra
  // byte for blocks 1-9.
  var id = mobwrite.uniqueId();
  var paddingSize = (prefix + 'b%3A' + id + '+++' + '%0A%0A').length +
      2 * digits;
  // Compute length available for each block.
  var blockLength = mobwrite.get_maxchars - paddingSize;
  if (blockLength < 3) {
    if (mobwrite.debug) {
      mobwrite.log.error('mobwrite.get_maxchars too small to send data.');
    }
    // Override this setting (3 is minimum to send the indivisible '%25').
    blockLength = 3;
  }
  // Compute number of blocks.
  var bufferBlocks = Math.ceil(encEncData.length / blockLength);
  if (typeof opt_minBlocks != 'undefined') {
    bufferBlocks = Math.max(bufferBlocks, opt_minBlocks);
  }
  // Obtain a random ID for this buffer.
  var bufferHeader = 'b%3A' + id + '+' +
      encodeURIComponent(bufferBlocks) + '+';
  var startPointer = 0;
  for (var x = 1; x <= bufferBlocks; x++) {
    var endPointer = startPointer + blockLength;
    // Don't split a '%25' construct.
    if (encEncData.charAt(endPointer - 1) == '%') {
      endPointer -= 1;
    } else if (encEncData.charAt(endPointer - 2) == '%') {
      endPointer -= 2;
    }
    var bufferData = encEncData.substring(startPointer, endPointer);
    blocks.push(prefix + bufferHeader + x + '+' + bufferData + '%0A%0A');
    startPointer = endPointer;
  }
  if (startPointer < encEncData.length) {
    if (mobwrite.debug) {
      mobwrite.log.debug('Recursing splitBlocks_ at n=' + (bufferBlocks + 1));
    }
    return this.splitBlocks_(data, bufferBlocks + 1);
  }
  return blocks;
};


/**
 * Callback location for JSON-P requests.
 * @param {string} text Raw content from server.
 */
mobwrite.callback = function(text) {
  // Only process the response if there is a response (don't schedule a new
  // heartbeat due to one of the many null responses from a buffer push).
  if (text) {
    // Add required trailing blank line.
    mobwrite.syncRun2_(text + '\n');
  } else {
    // This null response proves we got a round-trip of a buffer from the
    // server.  Reschedule the watchdog.
    window.clearTimeout(mobwrite.syncKillPid_);
    mobwrite.syncKillPid_ =
        window.setTimeout(mobwrite.syncKill_, mobwrite.timeoutInterval);
  }
};


/**
 * Parse all server-side changes and distribute them to the shared objects.
 * @param {string} text Raw content from server.
 * @private
 */
mobwrite.syncRun2_ = function(text) {
  // Initialize serverChange_, to be checked at the end of syncRun2_.
  mobwrite.serverChange_ = false;
  if (mobwrite.debug) {
    mobwrite.log.info('FROM server:\n' + text);
  }
  // Opera doesn't know how to decode char 0. (fixed in Opera 9.63)
  text = text.replace(/%00/g, '\0');
  // There must be a linefeed followed by a blank line.
  if (text.length < 2 || text.substring(text.length - 2) != '\n\n') {
    text = '';
    if (mobwrite.debug) {
      mobwrite.log.warn('Truncated data.  Abort.');
    }
  }
  var lines = text.split('\n');
  var file = null;
  var clientVersion = null;
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i];
    if (!line) {
      // Terminate on blank line.
      break;
    }
    // Divide each line into 'N:value' pairs.
    if (line.charAt(1) != ':') {
      if (mobwrite.debug) {
        mobwrite.log.error('Unparsable line: ' + line);
      }
      continue;
    }
    var name = line.charAt(0);
    var value = line.substring(2);

    // Parse out a version number for file, delta or raw.
    var version;
    if ('FfDdRr'.indexOf(name) != -1) {
      var div = value.indexOf(':');
      if (div < 1) {
        if (mobwrite.debug) {
          mobwrite.log.error('No version number: ' + line);
        }
        continue;
      }
      version = parseInt(value.substring(0, div), 10);
      if (isNaN(version)) {
        if (mobwrite.debug) {
          mobwrite.log.error('NaN version number: ' + line);
        }
        continue;
      }
      value = value.substring(div + 1);
    }
    if (name == '!') {
      if (mobwrite.shared.hasOwnProperty(value)) {
        try {
          // Pretend the view has been closed. This tells the server to
          // release any existing ViewObj instances and calls mobwrite.unshare
          let shareObj = mobwrite.shared[value];
          shareObj.unshare();
        } catch(e) {
          // shareObj might not exist anymore
          mobwrite.log.warn('Could not clean up document ' + value + ': ' +
                              e.message);
        }
      }
    }
    if (name == 'F' || name == 'f') {
      // File indicates which shared object following delta/raw applies to.
      if (value.substring(0, mobwrite.idPrefix.length) == mobwrite.idPrefix) {
        // Trim off the ID prefix.
        value = value.substring(mobwrite.idPrefix.length);
      } else {
        // This file does not have our ID prefix.
        file = null;
        if (mobwrite.debug) {
          mobwrite.log.error('File does not have "' + mobwrite.idPrefix +
              '" prefix: ' + value);
        }
        continue;
      }
      if (mobwrite.shared.hasOwnProperty(value)) {
        file = mobwrite.shared[value];
        file.deltaOk = true;
        clientVersion = version;
        // Remove any elements from the edit stack with low version numbers
        // which have been acked by the server.
        for (var x = 0; x < file.editStack.length; x++) {
          if (file.editStack[x][0] <= clientVersion) {
            file.editStack.splice(x, 1);
            x--;
          }
        }

      } else {
        // This file does not map to a currently shared object.
        file = null;
        if (mobwrite.debug) {
          mobwrite.log.error('Unknown file: ' + value);
        }
      }
    } else if (name == 'R' || name == 'r') {
      // The server reports it was unable to integrate the previous delta.
      if (file) {
        file.shadowText = decodeURI(value);
        file.clientVersion = clientVersion;
        file.serverVersion = version;
        file.editStack = [];
        if (name == 'R') {
          // Accept the server's raw text dump and wipe out any user's changes.
          file.setClientText(file.shadowText);
        }
        // Server-side activity.
        mobwrite.serverChange_ = true;
      }
    } else if (name == 'D' || name == 'd') {
      // The server offers a compressed delta of changes to be applied.
      if (file) {
        if (clientVersion != file.clientVersion) {
          // Can't apply a delta on a mismatched shadow version.
          file.deltaOk = false;
          if (mobwrite.debug) {
            mobwrite.log.error('Client version number mismatch.\n' +
                'Expected: ' + file.clientVersion + ' Got: ' + clientVersion);
          }
        } else if (version > file.serverVersion) {
          // Server has a version in the future?
          file.deltaOk = false;
          if (mobwrite.debug) {
            mobwrite.log.error('Server version in future.\n' +
                'Expected: ' + file.serverVersion + ' Got: ' + version);
          }
        } else if (version < file.serverVersion) {
          // We've already seen this diff.
          if (mobwrite.debug) {
            mobwrite.log.warn('Server version in past.\n' +
                'Expected: ' + file.serverVersion + ' Got: ' + version);
          }
        } else {
          // Expand the delta into a diff using the client shadow.
          var diffs;
          try {
            diffs = file.dmp.diff_fromDelta(file.shadowText, value);
            file.serverVersion++;
          } catch (ex) {
            // The delta the server supplied does not fit on our copy of
            // shadowText.
            diffs = null;
            // Set deltaOk to false so that on the next sync we send
            // a complete dump to get back in sync.
            file.deltaOk = false;
            // Do the next sync soon because the user will lose any changes.
            mobwrite.syncInterval = 0;
            if (mobwrite.debug) {
              mobwrite.log.error('Delta mismatch.\n' + encodeURI(file.shadowText));
            }
          }
          if (diffs && (diffs.length != 1 || diffs[0][0] != DIFF_EQUAL)) {
            // Compute and apply the patches.
            if (name == 'D') {
              // Overwrite text.
              file.shadowText = file.dmp.diff_text2(diffs);
              file.setClientText(file.shadowText);
            } else {
              // Merge text.
              var patches = file.dmp.patch_make(file.shadowText, diffs);
              // First shadowText.  Should be guaranteed to work.
              var serverResult = file.dmp.patch_apply(patches, file.shadowText);
              file.shadowText = serverResult[0];
              // Second the user's text.
              file.patchClientText(patches);
            }
            // Server-side activity.
            mobwrite.serverChange_ = true;
          }
        }
      }
    } else if (name == 'm' || name == 'M') {
      if (file) {
        try {
          var metadata = JSON.parse(value);
        } catch(e) {}
      }
    }
  }
  
  if (metadata) {
    file.handleMetadata(metadata);
  }


  mobwrite.computeSyncInterval_();
  // Check if another sync is necessary
  if (mobwrite.syncInterval >= 0) {
    // Ensure that there is only one sync task.
    window.clearTimeout(mobwrite.syncRunPid_);
    // Schedule the next sync.
    mobwrite.syncRunPid_ =
          window.setTimeout(mobwrite.syncRun1_, mobwrite.syncInterval);
  } else {
    // Ensure that there is only one sync task.
    window.clearTimeout(mobwrite.syncRunPid_);
    // Delete the sync task, and let certain events schedule a new task.
    mobwrite.syncRunPid_ = null;
  }

  // Terminate the watchdog task, everything's ok.
  window.clearTimeout(mobwrite.syncKillPid_);
  mobwrite.syncKillPid_ = null;
};


/**
 * Compute how long to wait until next synchronization.
 * @private
 */
mobwrite.computeSyncInterval_ = function() {

  if (mobwrite.clientChange_)
    // There were local changes during the last sync. Sync now.
    mobwrite.syncInterval = mobwrite.minSyncInterval;
  else
    // Don't run another sync immediately. Let the polling runner do its work.
    mobwrite.syncInterval = -1;
};


/**
 * If the Ajax call doesn't complete after a timeout period, start over.
 * @private
 */
mobwrite.syncKill_ = function() {
  mobwrite.syncKillPid_ = null;
  if (mobwrite.syncAjaxObj_) {
    // Cleanup old Ajax connection.
    mobwrite.syncAjaxObj_.abort();
    mobwrite.syncAjaxObj_ = null;
  }
  if (mobwrite.debug) {
    mobwrite.log.warn('Connection timeout.');
  }
  window.clearTimeout(mobwrite.syncRunPid_);
  // Initiate a new sync right now.
  mobwrite.syncRunPid_ = window.setTimeout(mobwrite.syncRun1_, 1);
};


/**
 * Initiate an Ajax network connection.
 * @param {string} url Location to send request.
 * @param {string} post Data to be sent.
 * @param {Function} callback Function to be called when response arrives.
 * @return {Object?} New Ajax object or null if failure.
 * @private
 */
mobwrite.syncLoadAjax_ = function(url, post, callback) {
  var req = null;
  // branch for native XMLHttpRequest object
  if (window.XMLHttpRequest) {
    try {
      req = new XMLHttpRequest();
    } catch(e1) {
      req = null;
    }
    // branch for IE/Windows ActiveX version
    } else if (window.ActiveXObject) {
    try {
      req = new ActiveXObject('Msxml2.XMLHTTP');
    } catch(e2) {
      try {
        req = new ActiveXObject('Microsoft.XMLHTTP');
      } catch(e3) {
        req = null;
      }
    }
  }
  if (req) {
    req.onreadystatechange = callback;
    req.open('POST', url, true);
    let cred = "komodo-sso " + ko.collaboration.service.ssoKey;
    req.setRequestHeader('Authorization', cred);
    req.setRequestHeader('Content-Type','application/x-www-form-urlencoded');
    req.send(post);
  }
  return req;
};


/**
 * Callback function for Ajax request.  Checks network response was ok,
 * then calls mobwrite.syncRun2_.
 * @private
 */
mobwrite.syncCheckAjax_ = function() {
  if (typeof mobwrite == 'undefined' || !mobwrite.syncAjaxObj_) {
    // This might be a callback after the page has unloaded,
    // or this might be a callback which we deemed to have timed out.
    return;
  }
  // Only if req shows "loaded"
  if (mobwrite.syncAjaxObj_.readyState == 4) {
    var obsSvc = Components.classes['@mozilla.org/observer-service;1'].
                    getService(Components.interfaces.nsIObserverService);
    // Only if "OK"
    if (mobwrite.syncAjaxObj_.status == 200) {
      var text = mobwrite.syncAjaxObj_.responseText;
      mobwrite.syncAjaxObj_ = null;
      // If there was connection error before, clear it, and notify observers.
      if (mobwrite.connectionError) {
        mobwrite.connectionError = false;
        obsSvc.notifyObservers(this, "collaboration:sync-connection-ok", null);
      }
      mobwrite.syncRun2_(text);
    } else {
      if (mobwrite.debug) {
        mobwrite.log.warn('Connection error code: ' + mobwrite.syncAjaxObj_.status);
      }
      if (mobwrite.syncAjaxObj_.status == 403) {
        obsSvc.notifyObservers(this, "services:sso:check-session", null);
      }
      mobwrite.connectionError = true;
      obsSvc.notifyObservers(this, "collaboration:sync-connection-error", null);
      mobwrite.syncAjaxObj_ = null;
      window.clearTimeout(mobwrite.syncRunPid_);
      mobwrite.syncRunPid_ = null;
      window.clearTimeout(mobwrite.syncKillPid_);
      mobwrite.syncKillPid_ = null;
    }
  }
};


/**
 * When unloading, run a sync one last time.
 * @private
 */
mobwrite.unload_ = function() {
  if (mobwrite.pollingRunPid && !mobwrite.syncKillPid_) {
    // Turn off debug mode since the console disappears on page unload before
    // this code does.
    mobwrite.debug = false;
    mobwrite.syncRun1_();
  }
  // By the time the callback runs mobwrite.syncRun2_, this page will probably
  // be gone.  But that's ok, we are just sending our last changes out, we
  // don't care what the server says.
};


// Attach unload event to window.
if (window.addEventListener) {  // W3
  window.addEventListener('unload', mobwrite.unload_, false);
} else if (window.attachEvent) {  // IE
  window.attachEvent('onunload', mobwrite.unload_);
}


/**
 * Start sharing the specified object(s).
 * @param {*} var_args Object(s) or ID(s) of object(s) to share.
 */
mobwrite.share = function(var_args) {
  for (var i = 0; i < arguments.length; i++) {
    var el = arguments[i];
    var result = null;
    // Ask every registered handler if it knows what to do with this object.
    for (var x = 0; x < mobwrite.shareHandlers.length && !result; x++) {
      result = mobwrite.shareHandlers[x].call(mobwrite, el);
    }
    if (result && result.file) {
      //if (!result.file.match(/^[A-Za-z][-.:\w]*$/)) {
      if (!result.file.match(/^[-.:\w]*$/)) {
        if (mobwrite.debug) {
          mobwrite.log.error('Illegal id "' + result.file + '".');
        }
        continue;
      }
      if (result.file in mobwrite.shared) {
        // Already exists.
        // Don't replace, since we don't want to lose state.
        if (mobwrite.debug) {
          mobwrite.log.warn('Ignoring duplicate share on "' + el + '".');
        }
        continue;
      }
      mobwrite.shared[result.file] = result;

      if (mobwrite.syncRunPid_ === null) {
        // Startup the main task if it doesn't already exist.
        if (mobwrite.debug) {
          mobwrite.log.info('MobWrite task started.');
        }
      } else {
        // Bring sync forward in time.
        window.clearTimeout(mobwrite.syncRunPid_);
      }
      mobwrite.syncRunPid_ = window.setTimeout(mobwrite.syncRun1_, 10);
      mobwrite.pollingRunPid = window.setTimeout(mobwrite.pollingRun, 
                                                 mobwrite.pollingInterval);
    } else {
      if (mobwrite.debug) {
        mobwrite.log.warn('Share: Unknown widget type: ' + el + '.');
      }
    }
  }
};


/**
 * Stop sharing the specified object(s).
 * Does not handle forms recursively.
 * @param {*} var_args Object(s) or ID(s) of object(s) to unshare.
 */
mobwrite.unshare = function(var_args) {
  for (var i = 0; i < arguments.length; i++) {
    var el = arguments[i];
    if (typeof el == 'string' && mobwrite.shared.hasOwnProperty(el)) {
      delete mobwrite.shared[el];
      if (mobwrite.debug) {
        mobwrite.log.info('Unshared: ' + el);
      }
    } else {
      // Pretend to want to share this object, acquire a new shareObj, then use
      // its ID to locate and kill the existing shareObj that's already shared.
      var result = null;
      // Ask every registered handler if it knows what to do with this object.
      for (var x = 0; x < mobwrite.shareHandlers.length && !result; x++) {
        result = mobwrite.shareHandlers[x].call(mobwrite, el);
      }
      if (result && result.file) {
        if (mobwrite.shared.hasOwnProperty(result.file)) {
          delete mobwrite.shared[result.file];
          if (mobwrite.debug) {
            mobwrite.log.info('Unshared: ' + el);
          }
        } else {
          if (mobwrite.debug) {
            mobwrite.log.warn('Ignoring ' + el + '. Not currently shared.');
          }
        }
      } else {
        if (mobwrite.debug) {
          mobwrite.log.warn('Unshare: Unknown widget type: ' + el + '.');
        }
      }
    }
  }
};

mobwrite.pollingRunPid = null;
mobwrite.pollingRun = function() {
  // When there is absolutely no other activity (local changes, push
  // notifications), sync at least once every few seconds to prevent the server
  // form cleaning up our ViewObj in case the client is idling.
  
  if (!mobwrite.syncKillPid_ && !mobwrite.syncRunPid_) {
    // No other sync task running or scheduled, start one immediately.
    mobwrite.syncRunPid_ = window.setTimeout(mobwrite.syncRun1_, 0);
  }
  window.clearTimeout(mobwrite.pollingRunPid);
  mobwrite.pollingRunPid = window.setTimeout(mobwrite.pollingRun,
                                          mobwrite.pollingInterval);
};

/**
 * Returns `true` if this client is logged in to SSO or false otherwise.
 */
mobwrite._checkLoginStatus = function() {
  if (!ko.collaboration.service.loggedIn) {
    return false;
  } else {
    return true;
  }
};


mobwrite.pauseSyncTask = function() {
  if (mobwrite.syncRunPid_ || mobwrite.pollingRunPid) {
    window.clearTimeout(mobwrite.syncRunPid_);
    mobwrite.syncRunPid_ = null;
    window.clearTimeout(mobwrite.pollingRunPid);
    mobwrite.pollingRunPid = null;
    
    mobwrite.connectionError = true;
    var obsSvc = Components.classes['@mozilla.org/observer-service;1'].
      getService(Components.interfaces.nsIObserverService);
    obsSvc.notifyObservers(this, "collaboration:sync-connection-error", null);
  }
};


mobwrite.resumeSyncTask = function() {
  var empty = true;
  for (var x in mobwrite.shared) {
    empty = false;
    break;
  }
  if (!empty) {
    mobwrite.syncNow();
    window.clearTimeout(mobwrite.pollingRunPid);
    mobwrite.pollingRunPid = window.setTimeout(mobwrite.pollingRun,
                                            mobwrite.pollingInterval);
  }
};


/**
 * Schedules an immediate sync run if there is no pending run.
 */
mobwrite.syncNow = function(delay) {
  if (!mobwrite.syncKillPid_ && !mobwrite.syncRunPid_) {
    // No running sync task, schedule one.
    window.clearTimeout(mobwrite.syncRunPid_);
    mobwrite.syncRunPid_ = window.setTimeout(mobwrite.syncRun1_, 150);
  } else {
    // Sync is running. Sync again when it's done.
    mobwrite.clientChange_ = true;
  }
};

mobwrite.observe = function(subject, topic, data) {
  switch (topic) {
    case "collaboration:force-reconnect":
      mobwrite.syncNow();
      break;
  }
};

mobwrite.addObserver = function() {
  var obsSvc = Components.classes['@mozilla.org/observer-service;1']
    .getService(Components.interfaces.nsIObserverService);
  obsSvc.addObserver(mobwrite, "collaboration:force-reconnect", false);
};

mobwrite.removeObserver = function() {
  var obsSvc = Components.classes['@mozilla.org/observer-service;1']
    .getService(Components.interfaces.nsIObserverService);
  obsSvc.removeObserver(mobwrite, "collaboration:force-reconnect");
}

window.setTimeout(mobwrite.addObserver, 0); // mobwrite is loaded lazily
window.addEventListener('unload', function() { mobwrite.removeObserver() }, false);
