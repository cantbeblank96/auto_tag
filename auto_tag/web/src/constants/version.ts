/** 应用版本（构建时从 auto_tag/constant.py 注入，勿在此手改）。 */
export const APP_VERSION: string = __APP_VERSION__

export function formatAppVersion(withPrefix = true): string {
  return withPrefix ? `v${APP_VERSION}` : APP_VERSION
}
