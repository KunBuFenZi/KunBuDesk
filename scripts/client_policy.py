"""Apply shared client UI policy while retaining accessible upstream legal notices."""
from __future__ import annotations

import html
import json
from pathlib import Path
import re

from configure import replace_one


FLUTTER_LICENSE_HELPERS = '''bool customSimplifiedAbout() =>
    bind.mainGetBuildinOption(key: 'simplify-about') == 'Y';

String customOpenSourceLicenseLabel() => translate('Open source licenses');

bool _customLicensesRegistered = false;

void showCustomOpenSourceLicenses(BuildContext context) {
  if (!_customLicensesRegistered) {
    LicenseRegistry.addLicense(() async* {
      final notices = await rootBundle.loadString('assets/custom-open-source-notices.txt');
      yield LicenseEntryWithLineBreaks(<String>['RustDesk'], notices);
    });
    _customLicensesRegistered = true;
  }
  showLicensePage(
    context: context,
    applicationName: bind.mainGetAppNameSync(),
  );
}

Widget customOpenSourceLicenseLink(BuildContext context) => InkWell(
  onTap: () => showCustomOpenSourceLicenses(context),
  child: Text(
    customOpenSourceLicenseLabel(),
    style: const TextStyle(decoration: TextDecoration.underline),
  ).marginSymmetric(vertical: 4.0),
);

'''


def require_support(source: Path):
    expected = {
        "libs/hbb_common/src/config.rs": (
            "res.extend(OVERWRITE_SETTINGS.read().unwrap().clone());",
            "get_or(\n            &OVERWRITE_SETTINGS,",
            "v.retain(|k, v| is_option_can_save(&OVERWRITE_SETTINGS, k, &DEFAULT_SETTINGS, v));",
            "if !is_option_can_save(&OVERWRITE_SETTINGS, &k, &DEFAULT_SETTINGS, &v) {",
            "if overwrite.read().unwrap().contains_key(k)",
            ".get(k)\n        .or(b.get(k))\n        .or(c.read().unwrap().get(k))",
        ),
        "flutter/lib/common.dart": ('key: "hide-powered-by-me"',),
        "flutter/lib/desktop/pages/desktop_setting_page.dart": ("key: kOptionHideServerSetting",),
        "flutter/lib/mobile/pages/settings_page.dart": ("key: kOptionHideServerSetting",),
        "src/ui/index.tis": ('get_builtin_option("hide-server-settings")', 'get_builtin_option("hide-powered-by-me")'),
    }
    for relative, anchors in expected.items():
        text = (source / relative).read_text(encoding="utf-8")
        if any(anchor not in text for anchor in anchors):
            raise ValueError(f"Upstream changed: client policy support missing in {relative}")


def apply_interface_policy(source: Path, config: dict):
    require_support(source)
    if config["lock_server_settings"]:
        path = source / "flutter/lib/mobile/widgets/dialog.dart"
        text = path.read_text(encoding="utf-8")
        old = "    void Function(VoidCallback)? upSetState) async {\n"
        new = old + '''  if (bind.mainGetBuildinOption(key: 'hide-server-settings') == 'Y') {
    return;
  }
'''
        path.write_text(replace_one(text, old, new, path), encoding="utf-8")

    if not config["simplify_about"]:
        return

    sciter_path = source / "src/ui/index.tis"
    sciter = sciter_path.read_text(encoding="utf-8")
    copyright = re.findall(r"Copyright &copy; [0-9]{4} Purslane Tech Pte\. Ltd\.", sciter)
    if len(copyright) != 1:
        raise ValueError("Upstream changed: original copyright notice")
    notices = (
        html.unescape(copyright[0]) + "\n\n"
        + config["app_name"] + " is a modified version of RustDesk.\n"
        + "Original project: https://github.com/rustdesk/rustdesk\n"
        + "Licensed under the GNU Affero General Public License, version 3.\n"
        + "This software is provided without warranty.\n\n"
        + (source / "LICENCE").read_text(encoding="utf-8")
    )
    # Upstream includes all files directly under assets/, so no pubspec edit is needed.
    pubspec = (source / "flutter/pubspec.yaml").read_text(encoding="utf-8")
    if "    - assets/\n" not in pubspec:
        raise ValueError("Upstream changed: Flutter assets directory registration")
    (source / "flutter/assets/custom-open-source-notices.txt").write_text(notices, encoding="utf-8")

    path = source / "flutter/lib/common.dart"
    text = path.read_text(encoding="utf-8")
    text = replace_one(text, "Widget loadPowered(BuildContext context) {", FLUTTER_LICENSE_HELPERS + "Widget loadPowered(BuildContext context) {", path)
    path.write_text(text, encoding="utf-8")

    path = source / "flutter/lib/desktop/pages/desktop_setting_page.dart"
    text = path.read_text(encoding="utf-8")
    begin = "              InkWell(\n                  onTap: () {\n                    launchUrlString('https://rustdesk.com/privacy.html');"
    end = "              ).marginSymmetric(vertical: 4.0)\n            ],\n          ).marginOnly(left: _kContentHMargin)"
    if text.count(begin) != 1 or text.count(end) != 1:
        raise ValueError("Upstream changed: desktop About links/banner")
    start = text.index(begin)
    finish = text.index(end, start) + len("              ).marginSymmetric(vertical: 4.0)")
    block = text[start:finish]
    replacement = "              if (!customSimplifiedAbout()) ...[\n" + block + "\n              ] else\n                customOpenSourceLicenseLink(context)"
    text = replace_one(text, block, replacement, path)
    path.write_text(text, encoding="utf-8")

    path = source / "flutter/lib/mobile/pages/settings_page.dart"
    text = path.read_text(encoding="utf-8")
    old = '''            SettingsTile(
                onPressed: (context) async {
                  await launchUrl(Uri.parse(url));
                },
                title: Text(translate("Version: ") + version),
                value: Padding('''
    new = old.replace("onPressed: (context)", "onPressed: customSimplifiedAbout() ? null : (context)")
    new = new.replace("value: Padding(", "value: customSimplifiedAbout() ? null : Padding(")
    text = replace_one(text, old, new, path)
    old = '''            SettingsTile(
              title: Text(translate("Privacy Statement")),
              onPressed: (context) =>
                  launchUrlString('https://rustdesk.com/privacy.html'),
              leading: Icon(Icons.privacy_tip),
            )'''
    new = "            if (!customSimplifiedAbout())\n" + old + '''
            else
              SettingsTile(
                title: Text(customOpenSourceLicenseLabel()),
                onPressed: (context) => showCustomOpenSourceLicenses(context),
                leading: Icon(Icons.description_outlined),
              )'''
    text = replace_one(text, old, new, path)
    old = '''        InkWell(
            onTap: () async {
              const url = 'https://rustdesk.com/';
              await launchUrl(Uri.parse(url));
            },
            child: Padding(
              padding: EdgeInsets.symmetric(vertical: 8),
              child: Text('rustdesk.com',
                  style: TextStyle(
                    decoration: TextDecoration.underline,
                  )),
            ))'''
    new = "        if (!customSimplifiedAbout())\n" + old + "\n        else\n          customOpenSourceLicenseLink(context)"
    text = replace_one(text, old, new, path)
    path.write_text(text, encoding="utf-8")

    old = "    function showAbout() {\n        var name = handler.get_app_name();\n"
    new = old + '''        if (handler.get_builtin_option("simplify-about") == "Y") {
            msgbox("custom-nocancel-nook-hasclose", translate("About") + " " + name,
                "<div style='line-height: 2em'><div>Version: " + handler.get_version() + "</div>"
                + "<div>Fingerprint: " + handler.get_fingerprint() + "</div>"
                + "<div .link .custom-event id='open-source-licenses'>" + translate("Open source licenses") + "</div></div>",
                "", function(el) {
                    if (el && el.attributes && el.attributes['id'] == 'open-source-licenses') {
                        showCustomOpenSourceLicenses();
                    }
                }, 400, get_msgbox_width());
            return;
        }
'''
    sciter = replace_one(sciter, old, new, sciter_path)
    content = "<div style='white-space: pre-wrap; overflow: auto;'>" + html.escape(notices) + "</div>"
    sciter += "\nfunction showCustomOpenSourceLicenses() {\n"
    sciter += '    msgbox("custom-nocancel-nook-hasclose", translate("Open source licenses"), ' + json.dumps(content, ensure_ascii=False) + ', "", null, 500, get_msgbox_width());\n}\n'
    sciter_path.write_text(sciter, encoding="utf-8")

    for relative, label in (("src/lang/cn.rs", "开源许可"), ("src/lang/tw.rs", "開源授權")):
        path = source / relative
        text = path.read_text(encoding="utf-8")
        match = re.findall(r'^        \("Website", "[^"\n]*"\),$', text, re.MULTILINE)
        if len(match) != 1:
            raise ValueError(f"Upstream changed: About label localization in {relative}")
        text = replace_one(text, match[0], match[0] + f'\n        ("Open source licenses", "{label}"),', path)
        path.write_text(text, encoding="utf-8")
