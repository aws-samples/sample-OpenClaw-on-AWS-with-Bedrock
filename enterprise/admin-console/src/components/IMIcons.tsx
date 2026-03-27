import React from 'react';
/**
 * Official brand SVG icons for IM channels.
 * Simplified from official brand guidelines, correct colors.
 */

export function TelegramIcon({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="12" fill="#2AABEE"/>
      <path d="M5.5 11.8l12.1-4.7c.6-.2 1.1.1.9.8l-2 9.5c-.2.7-.6.9-1.1.6l-3-2.3-1.5 1.4c-.2.2-.4.3-.7.3l.2-3.2 5.5-5c.2-.2-.1-.4-.4-.2L8.5 14.5l-3-1c-.7-.2-.7-.7.0-1z" fill="white"/>
    </svg>
  );
}

export function DiscordIcon({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <rect width="24" height="24" rx="6" fill="#5865F2"/>
      <path d="M16.1 7.3A13 13 0 0013.1 6.5c-.1.2-.3.5-.4.7-1.1-.2-2.2-.2-3.3 0-.1-.2-.3-.5-.4-.7-1 .2-2 .5-3 .8C4.2 11.1 3.8 14.8 4 18c1.1.8 2.2 1.3 3.2 1.6.3-.4.5-.8.7-1.2-.4-.1-.7-.3-1.1-.5l.3-.2c2.1 1 4.5 1 6.6 0l.3.2c-.4.2-.7.4-1.1.5.2.4.4.8.7 1.2 1-.3 2.1-.8 3.2-1.6.2-3.7-.6-7.3-2.7-9.7zM9.3 16c-.8 0-1.5-.8-1.5-1.7s.7-1.7 1.5-1.7c.9 0 1.5.8 1.5 1.7S10.1 16 9.3 16zm5.4 0c-.8 0-1.5-.8-1.5-1.7s.7-1.7 1.5-1.7c.9 0 1.5.8 1.5 1.7S15.5 16 14.7 16z" fill="white"/>
    </svg>
  );
}

export function SlackIcon({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <path d="M5.8 14.3a1.8 1.8 0 01-1.8 1.8 1.8 1.8 0 01-1.8-1.8 1.8 1.8 0 011.8-1.8H5.8v1.8z" fill="#E01E5A"/>
      <path d="M6.7 14.3a1.8 1.8 0 011.8-1.8 1.8 1.8 0 011.8 1.8v4.5a1.8 1.8 0 01-1.8 1.8 1.8 1.8 0 01-1.8-1.8v-4.5z" fill="#E01E5A"/>
      <path d="M8.5 5.8a1.8 1.8 0 01-1.8-1.8A1.8 1.8 0 018.5 2.2a1.8 1.8 0 011.8 1.8V5.8H8.5z" fill="#36C5F0"/>
      <path d="M8.5 6.7a1.8 1.8 0 011.8 1.8 1.8 1.8 0 01-1.8 1.8H4a1.8 1.8 0 01-1.8-1.8A1.8 1.8 0 014 6.7h4.5z" fill="#36C5F0"/>
      <path d="M18.2 8.5a1.8 1.8 0 011.8 1.8 1.8 1.8 0 01-1.8 1.8 1.8 1.8 0 01-1.8-1.8V8.5h1.8z" fill="#2EB67D"/>
      <path d="M17.3 8.5a1.8 1.8 0 01-1.8 1.8 1.8 1.8 0 01-1.8-1.8V4a1.8 1.8 0 011.8-1.8 1.8 1.8 0 011.8 1.8v4.5z" fill="#2EB67D"/>
      <path d="M15.5 18.2a1.8 1.8 0 011.8 1.8 1.8 1.8 0 01-1.8 1.8 1.8 1.8 0 01-1.8-1.8v-1.8h1.8z" fill="#ECB22E"/>
      <path d="M15.5 17.3a1.8 1.8 0 01-1.8-1.8 1.8 1.8 0 011.8-1.8H20a1.8 1.8 0 011.8 1.8 1.8 1.8 0 01-1.8 1.8h-4.5z" fill="#ECB22E"/>
    </svg>
  );
}

export function TeamsIcon({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <rect width="24" height="24" rx="4" fill="#5059C9"/>
      <path d="M14.5 7.5a2 2 0 100-4 2 2 0 000 4z" fill="white"/>
      <path d="M14.5 8.5c-1.7 0-3 .9-3 2v4c0 .3.2.5.5.5h5c.3 0 .5-.2.5-.5v-4c0-1.1-1.3-2-3-2z" fill="white"/>
      <path d="M9.5 9.5a1.5 1.5 0 100-3 1.5 1.5 0 000 3z" fill="#7B83EB"/>
      <path d="M6 10.5c0-.8.7-1.5 1.5-1.5h3c.8 0 1.5.7 1.5 1.5V15c0 .3-.2.5-.5.5h-5c-.3 0-.5-.2-.5-.5v-4.5z" fill="#7B83EB"/>
      <rect x="6" y="10" width="6" height="6" rx="1" fill="#4B53BC"/>
    </svg>
  );
}

export function GoogleChatIcon({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <rect width="24" height="24" rx="6" fill="#00897B"/>
      <path d="M12 5C8.1 5 5 8.1 5 12s3.1 7 7 7h7v-7c0-3.9-3.1-7-7-7z" fill="white"/>
      <circle cx="9" cy="12" r="1.2" fill="#00897B"/>
      <circle cx="12" cy="12" r="1.2" fill="#00897B"/>
      <circle cx="15" cy="12" r="1.2" fill="#00897B"/>
    </svg>
  );
}

export function WhatsAppIcon({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="12" fill="#25D366"/>
      <path d="M17.5 14.4c-.3-.1-1.7-.8-2-.9-.3-.1-.4-.1-.6.1-.2.3-.6.9-.8 1.1-.1.2-.3.2-.6.1-.3-.1-1.2-.4-2.3-1.4-.9-.8-1.4-1.7-1.6-2-.2-.3 0-.4.1-.6l.4-.5c.1-.2.2-.3.3-.5 0-.2 0-.3-.1-.5-.1-.1-.6-1.5-.8-2-.2-.5-.4-.5-.6-.5h-.5c-.2 0-.5.1-.7.3-.3.3-1 1-1 2.4s1 2.8 1.2 3c.1.1 2 3.1 4.9 4.3.7.3 1.2.5 1.6.6.7.2 1.3.2 1.8.1.5-.1 1.7-.7 1.9-1.4.2-.6.2-1.2.1-1.3-.1-.1-.3-.2-.6-.3z" fill="white"/>
    </svg>
  );
}

export function WeChatIcon({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="12" fill="#07C160"/>
      <path d="M9.5 7C7 7 5 8.8 5 11c0 1.2.6 2.3 1.6 3l-.4 1.5 1.6-.8c.5.1 1 .2 1.6.2.2 0 .4 0 .6-.1-.1-.3-.1-.6-.1-.9 0-2.2 2-4 4.5-4h.1C14.1 8.3 12 7 9.5 7z" fill="white"/>
      <path d="M14.5 10.5c-2.2 0-4 1.5-4 3.5 0 1.9 1.8 3.5 4 3.5.5 0 1-.1 1.5-.2l1.4.7-.4-1.3c.9-.6 1.5-1.6 1.5-2.7 0-2-1.8-3.5-4-3.5z" fill="white"/>
    </svg>
  );
}

export function FeishuIcon({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <rect width="24" height="24" rx="6" fill="#1456F0"/>
      <path d="M7 13l3-7 3 5-3 2 1 4-4-4z" fill="white" opacity="0.9"/>
      <path d="M12 11l3-5 2 7-4-2z" fill="white" opacity="0.7"/>
    </svg>
  );
}

export const IM_ICONS: Record<string, (props: { size?: number }) => React.ReactElement> = {
  telegram: TelegramIcon,
  discord: DiscordIcon,
  slack: SlackIcon,
  teams: TeamsIcon,
  googlechat: GoogleChatIcon,
  whatsapp: WhatsAppIcon,
  wechat: WeChatIcon,
  feishu: FeishuIcon,
};
