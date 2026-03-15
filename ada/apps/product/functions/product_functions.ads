with Arthexis.ORM;

package Apps.Product.Functions.Product_Functions is
   --  SQL-callable function registrations for the Product backbone app.

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection);
   --  Register Product backbone SQL functions and helper views.
end Apps.Product.Functions.Product_Functions;
