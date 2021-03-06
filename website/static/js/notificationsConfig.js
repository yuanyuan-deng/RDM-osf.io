'use strict';

var ko = require('knockout');
var $ = require('jquery');
var $osf = require('js/osfHelpers');

var _ = require('js/rdmGettext')._;
var sprintf = require('agh.sprintf').sprintf;

var ViewModel = function(list) {
    var self = this;
    self.list = list;
    self.subscribed = ko.observableArray();
    // Flashed messages
    self.message = ko.observable('');
    self.messageClass = ko.observable('text-success');

    self.getListInfo = function() {
        $.ajax({
            url: '/api/v1/settings/notifications',
            type: 'GET',
            dataType: 'json',
            success: function(response) {
                for (var key in response.mailing_lists){
                    if (response.mailing_lists[key]){
                        self.subscribed.push(key);
                    }
                }
            },
            error: function() {
                var message = _('Could not retrieve settings information.');
                self.changeMessage(message, 'text-danger', 5000);
            }});
    };

    self.getListInfo();

    /** Change the flashed status message */
    self.changeMessage = function(text, css, timeout) {
        self.message(text);
        var cssClass = css || 'text-info';
        self.messageClass(cssClass);
        if (timeout) {
            // Reset message after timeout period
            setTimeout(function() {
                self.message('');
                self.messageClass('text-info');
            }, timeout);
        }
    };

    self.submit = function () {
        var payload = {};
        for (var i in self.list){
            payload[self.list[i]] = $.inArray(self.list[i], self.subscribed()) !== -1;
        }
        var request = $osf.postJSON('/api/v1/settings/notifications/', payload);
        request.done(function () {
            self.changeMessage('Settings updated.', 'text-success', 5000);
        });
        request.fail(function (xhr) {
            if (xhr.responseJSON.error_type !== 'not_subscribed') {
                var message = _('Could not update email preferences at this time. If this issue persists, ') +
                    sprintf(_('please report it to %1$s.') , $osf.osfSupportLink());
                self.changeMessage(message, 'text-danger', 5000);
            }
        });
    };
};

// API
function NotificationsViewModel(selector, list) {
    var self = this;
    self.viewModel = new ViewModel(list);
    $osf.applyBindings(self.viewModel, selector);
}

module.exports = NotificationsViewModel;
