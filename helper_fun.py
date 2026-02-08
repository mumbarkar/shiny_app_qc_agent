def is_critical_error(error_text: str) -> bool:
    """
    Determine if an error is critical (actual app error) vs harmless (network/resource error).
    
    Args:
        error_text: The error message text
    
    Returns:
        bool: True if error is critical and should be reported, False if it's harmless
    """
    # Patterns that indicate harmless errors
    harmless_patterns = [
        "404",  # Not found (usually non-critical resources)
        "favicon",  # Missing favicon icon
        "analytics",  # Analytics script failure
        "tracking",  # Tracking script failure
        "advertisement",  # Ad block/ad errors
        "net::err_name_not_resolved",  # DNS resolution for external service
        "cors",  # Cross-origin resource sharing (external APIs)
        "failed to fetch",  # Network request failures (external resources)
        "xhr failed",  # XMLHttpRequest to external service
        "typekit",  # Font loading service
        "cloudflare",  # CDN errors
        "google",  # Google services (analytics, fonts, etc.)
    ]
    
    error_lower = error_text.lower()
    return not any(pattern in error_lower for pattern in harmless_patterns)
