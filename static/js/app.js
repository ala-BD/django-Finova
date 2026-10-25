function getCsrfToken() {
    const cookie = document.cookie.split("; ").find((item) => item.startsWith("csrftoken="));
    return cookie ? cookie.split("=")[1] : "";
}

window.finova = { getCsrfToken };
