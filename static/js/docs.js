// ============================================
// DOCUMENTATION JAVASCRIPT
// ============================================

document.addEventListener('DOMContentLoaded', function() {

    // ----- SEARCH FUNCTIONALITY -----
    const searchInput = document.getElementById('docsSearch');
    if (searchInput) {
        const sections = document.querySelectorAll('.docs-section');
        const navLinks = document.querySelectorAll('.docs-nav a');

        searchInput.addEventListener('input', function() {
            const query = this.value.toLowerCase().trim();

            sections.forEach(function(section) {
                const text = section.textContent.toLowerCase();
                if (query === '') {
                    section.style.display = '';
                } else {
                    section.style.display = text.includes(query) ? '' : 'none';
                }
            });

            // Also filter nav links
            navLinks.forEach(function(link) {
                const text = link.textContent.toLowerCase();
                if (query === '') {
                    link.style.display = '';
                } else {
                    link.style.display = text.includes(query) ? '' : 'none';
                }
            });
        });
    }

    // ----- SMOOTH SCROLL FOR NAV LINKS -----
    document.querySelectorAll('.docs-nav a').forEach(function(link) {
        link.addEventListener('click', function(e) {
            const targetId = this.getAttribute('href');
            if (targetId && targetId.startsWith('#')) {
                e.preventDefault();
                const target = document.querySelector(targetId);
                if (target) {
                    const headerOffset = 80;
                    const elementPosition = target.getBoundingClientRect().top;
                    const offsetPosition = elementPosition + window.pageYOffset - headerOffset;

                    window.scrollTo({
                        top: offsetPosition,
                        behavior: 'smooth'
                    });

                    // Update active link
                    document.querySelectorAll('.docs-nav a').forEach(function(a) {
                        a.classList.remove('active');
                    });
                    this.classList.add('active');
                }
            }
        });
    });

    // ----- HIGHLIGHT ACTIVE NAV ON SCROLL -----
    const sections = document.querySelectorAll('.docs-section');
    const navLinks = document.querySelectorAll('.docs-nav a');

    window.addEventListener('scroll', function() {
        let current = '';
        const scrollY = window.pageYOffset + 120;

        sections.forEach(function(section) {
            const sectionTop = section.offsetTop;
            const sectionHeight = section.offsetHeight;
            if (scrollY >= sectionTop && scrollY < sectionTop + sectionHeight) {
                current = '#' + section.getAttribute('id');
            }
        });

        navLinks.forEach(function(link) {
            link.classList.remove('active');
            if (link.getAttribute('href') === current) {
                link.classList.add('active');
            }
        });
    });

    // ----- MOBILE: CLOSE SIDEBAR ON LINK CLICK (optional) -----
    // For small screens, sidebar is already inline, so no toggle needed.
});