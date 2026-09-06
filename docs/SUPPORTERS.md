# Supporter listings

UsageLoop keeps sponsor recognition small and optional. The README and website
contain invisible markers for these listings, so an empty section never appears
on either page.

## Who can be listed

- A current $25 or $50 monthly sponsor can opt in to a public name and GitHub
  profile link in the README.
- A current $50 monthly sponsor can also opt in to a name or small logo and link
  on the project website.
- The sponsorship must be public. Never list a private sponsor or use a payment
  name, email address, or company name from the Sponsors dashboard.
- Use only the public identity and link the sponsor approved. A logo must be
  provided by the sponsor and should be a small PNG or WebP file.
- One-time sponsorships do not create an ongoing listing.

## Add or remove someone

1. Confirm that the sponsorship is public, the monthly tier is eligible, and the
   sponsor has opted in to the exact name, link, and optional logo.
2. Replace the content between the `supporters:start` and `supporters:end`
   markers in `README.md` with the README block below. Add every eligible $25
   and $50 sponsor who opted in.
3. For a $50 sponsor, replace the matching markers in `docs/index.html` with the
   website block below. Use the text version unless the sponsor supplied a logo.
4. Remove a listing if the sponsorship becomes private, the sponsor opts out, or
   the monthly sponsorship ends. If the last listing is removed, restore the
   invisible marker comments so no empty section is shown.
5. Run `python -m unittest tests.test_product` and check both links before
   publishing.

## README block

```markdown
<!-- supporters:start -->
## Supporters

Thanks to the public sponsors helping keep UsageLoop moving.

- [Sponsor name](https://github.com/sponsor-login)
<!-- supporters:end -->
```

## Website block

Keep this after the final call to action and before `</main>`. The styling is
already in `styles.css` and stays deliberately quieter than the product content.

```html
<!-- supporters:start -->
<section class="supporters-strip" aria-labelledby="supporters-title">
  <h2 id="supporters-title">Supported by</h2>
  <div class="supporters-list">
    <a class="supporter-link" href="https://github.com/sponsor-login">Sponsor name</a>
    <a class="supporter-link" href="https://example.com">
      <img class="supporter-logo" src="supporters/sponsor-name.png" width="28" height="28" alt="">
      <span>Sponsor name</span>
    </a>
  </div>
</section>
<!-- supporters:end -->
```

Use either the text link or the logo version for each sponsor, not both. If a
logo is used, save it under `docs/supporters/` with a simple lowercase filename.
