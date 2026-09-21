import { createUniver, LocaleType, mergeLocales } from '@univerjs/presets';
import { UniverDocsCorePreset } from '@univerjs/preset-docs-core';
import UniverPresetDocsCoreZhCN from '@univerjs/preset-docs-core/locales/zh-CN';
import '@univerjs/preset-docs-core/lib/index.css';

export function createPrdUniver(container: HTMLElement) {
  return createUniver({
    locale: LocaleType.ZH_CN,
    locales: { [LocaleType.ZH_CN]: mergeLocales(UniverPresetDocsCoreZhCN) },
    // The simple ribbon merges the Start and Insert commands into one row.
    // Univer keeps overflowed commands in its built-in collapsed menu.
    presets: [UniverDocsCorePreset({ container, ribbonType: 'simple' })],
  });
}
