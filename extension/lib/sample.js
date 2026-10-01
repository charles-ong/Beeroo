// A small, made-up product response used ONLY for the on-screen preview.
// It deliberately includes fields we never send (cart state, long text,
// recommendations) so people can see the allowlist working.
export const SAMPLE_KIND = "bws_products";

export const SAMPLE_RESPONSE = {
  TotalRecordCount: 1,
  Items: [{
    Name: " Example Lager Bottles<br>330ml  ",
    PackParentStockCode: 100001,
    PackMessage: "",
    Products: [{
      Stockcode: 100001, Price: 5.5, WasPrice: 5.5, Name: "Example Lager Bottles 330ml",
      UrlFriendlyName: "example-lager-bottles-330ml", IsAvailable: true, PackageSize: "330ML",
      BrandName: "Example", PromotionType: null,
      QuantityInTrolley: 2, IsInTrolley: true, IsWatched: true,
      RichDescription: "A long marketing description that we never send...",
      RecommendedProducts: [{ Stockcode: 1, Name: "Something else" }],
      FixedPricePromoTag: { PromotionalPrice: 0, ProductMultiplier: 0, TagContent: "image-url" },
      AdditionalDetails: [
        { Name: "productunitquantity", Value: "1" },
        { Name: "alcohol%", Value: "4.5%" },
        { Name: "liquorsize", Value: "330ML" },
        { Name: "brand_name", Value: "Example" },
        { Name: "productcopy", Value: "More marketing text we never send" },
      ],
    }],
  }],
};
