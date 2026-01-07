import { ref, watch, onMounted } from 'vue';

export type ThemeMode = 'light' | 'dark';

const THEME_STORAGE_KEY = 'fire-monitor-theme';

// Global reactive state
const currentTheme = ref<ThemeMode>('dark');

/**
 * Theme management composable
 * Provides reactive theme state and toggle functionality
 */
export function useTheme() {
    /**
     * Get the initial theme from localStorage or system preference
     */
    const getInitialTheme = (): ThemeMode => {
        // Check localStorage first
        const stored = localStorage.getItem(THEME_STORAGE_KEY) as ThemeMode | null;
        if (stored === 'light' || stored === 'dark') {
            return stored;
        }

        // Check system preference
        if (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) {
            return 'light';
        }

        // Default to dark
        return 'dark';
    };

    /**
     * Apply theme to the document
     */
    const applyTheme = (theme: ThemeMode) => {
        const html = document.documentElement;

        // Add transition class for smooth theme change
        html.classList.add('theme-transition');

        // Set theme attribute
        html.setAttribute('data-theme', theme);

        // Remove transition class after animation
        setTimeout(() => {
            html.classList.remove('theme-transition');
        }, 300);

        // Persist to localStorage
        localStorage.setItem(THEME_STORAGE_KEY, theme);
    };

    /**
     * Toggle between light and dark themes
     */
    const toggleTheme = () => {
        currentTheme.value = currentTheme.value === 'dark' ? 'light' : 'dark';
    };

    /**
     * Set a specific theme
     */
    const setTheme = (theme: ThemeMode) => {
        currentTheme.value = theme;
    };

    /**
     * Check if current theme is dark
     */
    const isDark = () => currentTheme.value === 'dark';

    /**
     * Check if current theme is light
     */
    const isLight = () => currentTheme.value === 'light';

    // Watch for theme changes and apply
    watch(currentTheme, (newTheme) => {
        applyTheme(newTheme);
    }, { immediate: false });

    // Initialize theme on mount
    onMounted(() => {
        currentTheme.value = getInitialTheme();
        applyTheme(currentTheme.value);
    });

    // Listen for system theme changes
    if (typeof window !== 'undefined' && window.matchMedia) {
        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
            // Only auto-switch if user hasn't explicitly set a preference
            const stored = localStorage.getItem(THEME_STORAGE_KEY);
            if (!stored) {
                currentTheme.value = e.matches ? 'dark' : 'light';
            }
        });
    }

    return {
        theme: currentTheme,
        toggleTheme,
        setTheme,
        isDark,
        isLight
    };
}

/**
 * Initialize theme on app startup (call this in main.ts)
 */
export function initTheme() {
    const stored = localStorage.getItem(THEME_STORAGE_KEY) as ThemeMode | null;
    let theme: ThemeMode = 'dark';

    if (stored === 'light' || stored === 'dark') {
        theme = stored;
    } else if (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) {
        theme = 'light';
    }

    document.documentElement.setAttribute('data-theme', theme);
}
